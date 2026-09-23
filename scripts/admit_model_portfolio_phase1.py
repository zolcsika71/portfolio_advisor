"""Exercise Phase 1 contracts on an existing temporary schema-v3 database."""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
from collections.abc import Sequence
from dataclasses import asdict
from pathlib import Path

from portfolio_advisor.database.model_portfolio_phase1 import (
    ModelPortfolioPhase1Error,
    admit_workbook_normalization,
    install_phase1_schema,
    request_from_json,
)


def _temporary_database(path: Path) -> Path:
    if path.is_symlink() or not path.is_file():
        raise ModelPortfolioPhase1Error(
            "Phase 1 target must be an existing regular temporary database"
        )
    if path.stat().st_nlink != 1:
        raise ModelPortfolioPhase1Error(
            "Phase 1 target must not be a hard-linked database"
        )
    resolved = path.resolve()
    temporary_root = Path(tempfile.gettempdir()).resolve()
    try:
        resolved.relative_to(temporary_root)
    except ValueError as error:
        raise ModelPortfolioPhase1Error(
            "Phase 1 command accepts databases only below the system temporary directory"
        ) from error
    return resolved


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Admit synthetic Phase 1 model normalization on a temporary database"
    )
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--request", type=Path, required=True)
    args = parser.parse_args(argv)
    database = _temporary_database(args.database)
    if args.request.is_symlink() or not args.request.is_file():
        parser.error("request must be an existing regular JSON file")
    payload = json.loads(args.request.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        parser.error("request JSON must be an object")
    request = request_from_json(payload)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        install_phase1_schema(connection)
        result = admit_workbook_normalization(connection, request)
    print(json.dumps(asdict(result), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
