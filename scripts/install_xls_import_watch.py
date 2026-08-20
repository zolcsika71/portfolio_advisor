"""Validate or explicitly install the current-user XLS WatchPaths LaunchAgent."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from portfolio_advisor.operations.xls_import_watch_installation import (
    WATCH_TEMPLATE_PATH,
    XlsImportWatchInstallationError,
    install_xls_import_watch,
    resolve_watch_plist,
    validate_watch_plist,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    action = parser.add_mutually_exclusive_group(required=True)
    action.add_argument("--dry-run", action="store_true", help="Validate the generated plist without installing it.")
    action.add_argument("--install", action="store_true", help="Install only the matching current-user LaunchAgent.")
    args = parser.parse_args(argv)
    root = Path.cwd().resolve()
    try:
        if args.dry_run:
            content = resolve_watch_plist(root / WATCH_TEMPLATE_PATH, root, Path(sys.executable))
            fingerprint = validate_watch_plist(content, root, Path(sys.executable))
            print(f"DRY_RUN_VALIDATED label=com.portfolio_advisor.xls_import_watch fingerprint={fingerprint}")
            return 0
        result = install_xls_import_watch(
            template_path=root / WATCH_TEMPLATE_PATH,
            repository_root=root,
            python_executable=Path(sys.executable),
        )
    except XlsImportWatchInstallationError as error:
        print(f"XLS import watch installation failed: {error}", file=sys.stderr)
        return 2
    print(f"{result.status} label={result.job_identifier}")
    print(f"installed_path={result.installed_plist_path}")
    print(f"loaded={result.loaded} enabled={result.enabled}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
