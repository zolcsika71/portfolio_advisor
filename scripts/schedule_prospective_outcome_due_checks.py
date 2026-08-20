"""Generate one rolling, offline launchd template for the next live outcome due date.

Without ``--install`` the command only writes the project-owned template and
audit.  Explicit ``--install`` installs that validated template for the current
user.  Neither mode fetches a provider, admits an outcome, or alters the
financial ledger.
"""

from __future__ import annotations

import argparse
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from portfolio_advisor.prospective.due_schedule_installation import (
    install_prospective_due_schedule,
)
from portfolio_advisor.prospective.due_scheduling import (
    LAUNCHD_TEMPLATE_NAME,
    LAUNCHD_TEMPLATE_ROOT,
    build_prospective_outcome_due_schedule,
    write_prospective_outcome_due_schedule,
)
from portfolio_advisor.prospective.validation import (
    ProspectiveValidationError,
    ProspectiveValidationStore,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--as-of-date", type=date.fromisoformat)
    parser.add_argument("--dry-run", action="store_true", help="Validate and display the schedule without writing files.")
    parser.add_argument(
        "--install",
        action="store_true",
        help="Explicitly install and enable the identical generated template for the current user only.",
    )
    parser.add_argument("--store", type=Path, default=Path("database/prospective_portfolio_validation.sqlite"))
    parser.add_argument("--freeze", type=Path, default=Path("data/audit/portfolio_nav_reconstruction_freeze.json"))
    parser.add_argument("--output", type=Path, default=Path("data/audit/prospective_outcome_due_schedule.json"))
    parser.add_argument("--template", type=Path, default=LAUNCHD_TEMPLATE_ROOT / LAUNCHD_TEMPLATE_NAME)
    args = parser.parse_args(argv)
    as_of_date = args.as_of_date or datetime.now(ZoneInfo("Europe/Budapest")).date()
    try:
        schedule = build_prospective_outcome_due_schedule(
            store=ProspectiveValidationStore(args.store),
            repository_root=Path.cwd().resolve(),
            freeze_path=args.freeze,
            as_of_date=as_of_date,
        )
        repeated = build_prospective_outcome_due_schedule(
            store=ProspectiveValidationStore(args.store),
            repository_root=Path.cwd().resolve(),
            freeze_path=args.freeze,
            as_of_date=as_of_date,
        )
        if schedule != repeated:
            raise ProspectiveValidationError("due schedule output is nondeterministic")
        if args.dry_run and args.install:
            raise ProspectiveValidationError("--dry-run cannot be combined with --install")
        installation = None
        if not args.dry_run:
            write_prospective_outcome_due_schedule(
                artifact_path=args.output,
                template_path=args.template,
                schedule=schedule,
            )
            if args.install:
                if schedule["schedule_status"] != "PROSPECTIVE_OUTCOME_DUE_SCHEDULE_VALIDATED_WITH_CAVEATS":
                    raise ProspectiveValidationError("a current future live due slot is required before installation")
                installation = install_prospective_due_schedule(
                    template_path=args.template,
                    repository_root=Path.cwd().resolve(),
                )
    except (ProspectiveValidationError, RuntimeError, ValueError) as error:
        print(f"Prospective outcome due scheduling failed: {error}", file=sys.stderr)
        return 2
    print(f"Schedule status: {schedule['schedule_status']}")
    print(f"Next due: {schedule['next_due_date']} ({schedule['next_due_horizon']}d)")
    print(f"Job: {schedule['job_identifier']}; installation: {schedule['installation_state']}")
    print(f"Schedule fingerprint: {schedule['fingerprint']}")
    if installation is not None:
        print(f"Installation status: {installation.status}")
        print(f"Installed plist: {installation.installed_plist_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
