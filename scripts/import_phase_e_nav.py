"""Offline import of retained Phase E exact-share-class NAV evidence."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from portfolio_advisor.history.nav_provenance import (
    NavProvenanceError,
    import_phase_e_nav,
)

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", type=Path, required=True)
    parser.add_argument("--repository-root", type=Path, default=ROOT)
    parser.add_argument(
        "--index", type=Path,
        default=ROOT / "data/raw/nav/erste_market/phase-e-index.json",
    )
    arguments = parser.parse_args(argv)
    try:
        result = import_phase_e_nav(
            repository_root=arguments.repository_root,
            target=arguments.target,
            index_path=arguments.index,
        )
    except (NavProvenanceError, OSError, ValueError) as error:
        print(f"Phase E NAV import failed: {error}", file=sys.stderr)
        return 2
    print(json.dumps(result.to_dict(), ensure_ascii=True, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
