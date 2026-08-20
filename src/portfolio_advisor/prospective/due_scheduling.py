"""Rolling, offline scheduling evidence for prospective outcome due checks.

The scheduler intentionally writes only a project-owned launchd template.  It
does not install a user launch agent, invoke a provider, admit an outcome, or
change a finalized decision.
"""

from __future__ import annotations

import json
import os
import tempfile
from collections.abc import Mapping
from datetime import date
from pathlib import Path
from typing import Final

from .validation import (
    LIVE_RECORD,
    PENDING,
    ProspectiveValidationError,
    ProspectiveValidationStore,
    _artifact_reference,
    _fingerprint,
    _load_object,
)

SCHEDULE_SCHEMA_VERSION: Final = 1
SCHEDULE_VERSION: Final = "1.0.0"
LAUNCHD_JOB_IDENTIFIER: Final = "com.portfolio_advisor.prospective_outcome_due_check"
SCHEDULED_LOCAL_TIME: Final = "09:00"
LAUNCHD_TEMPLATE_ROOT: Final = Path("ops/launchd")
LAUNCHD_TEMPLATE_NAME: Final = f"{LAUNCHD_JOB_IDENTIFIER}.plist"
MONITOR_COMMAND: Final = (
    "poetry run python scripts/check_due_prospective_outcomes.py "
    "&& poetry run python scripts/audit_prospective_portfolio_validation.py"
)


def build_prospective_outcome_due_schedule(
    *,
    store: ProspectiveValidationStore,
    repository_root: Path,
    freeze_path: Path,
    as_of_date: date,
) -> dict[str, object]:
    """Build a single rolling schedule from future live pending slots only."""
    freeze = _load_object(freeze_path, "portfolio-NAV reconstruction freeze")
    if freeze.get("validation_status") != "PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED":
        raise ProspectiveValidationError("due scheduling requires the active reconstruction freeze")
    pending = _live_pending_slots(store)
    overdue = [item for item in pending if _expected_end_date(item) <= as_of_date]
    future = [item for item in pending if _expected_end_date(item) > as_of_date]
    due_slot = overdue[0] if overdue else None
    next_due = due_slot or (future[0] if future else None)
    schedule_required = next_due is not None and due_slot is None
    status = (
        "PROSPECTIVE_OUTCOME_DUE_SCHEDULE_VALIDATED_WITH_CAVEATS"
        if schedule_required
        else "PROSPECTIVE_OUTCOME_DUE_SCHEDULE_NOT_REQUIRED"
    )
    payload: dict[str, object] = {
        "schema_version": SCHEDULE_SCHEMA_VERSION,
        "schedule_version": SCHEDULE_VERSION,
        "schedule_status": status,
        "as_of_date": as_of_date.isoformat(),
        "scheduler_backend": "LAUNCHD_TEMPLATE",
        "installation_state": "GENERATED_ONLY_NOT_INSTALLED",
        "enabled": False,
        "job_identifier": LAUNCHD_JOB_IDENTIFIER,
        "scheduled_local_timezone": "Europe/Budapest",
        "scheduled_local_time": SCHEDULED_LOCAL_TIME,
        "monitor_command": MONITOR_COMMAND,
        "monitor_only_execution": True,
        "network_access": "NOT_USED",
        "research_backfills_excluded": True,
        "freeze_reference": _artifact_reference(freeze_path, repository_root),
        "freeze_status": freeze["validation_status"],
        "next_due_decision_id": next_due["decision_id"] if next_due else None,
        "next_due_horizon": next_due["horizon_days"] if next_due else None,
        "next_due_date": _expected_end_date(next_due).isoformat() if next_due else None,
        "current_slot_status": next_due["status"] if next_due else None,
        "overdue_pending_live_slot_count": len(overdue),
        "overdue_monitoring_required": bool(overdue),
        "scheduling_blocked_by_due_slot": due_slot is not None,
        "no_future_pending_live_slot": not future,
        "launchd_template": (
            (LAUNCHD_TEMPLATE_ROOT / LAUNCHD_TEMPLATE_NAME).as_posix() if schedule_required else None
        ),
        "reschedule_contract": {
            "mode": "ONE_ROLLING_FUTURE_CHECK",
            "after_monitor": "Run this scheduler again to derive and generate the next future pending live slot.",
            "missed_run": "The offline monitor remains authoritative and classifies a later pending slot as DUE_UNASSESSED.",
        },
        "blocked_actions": [
            "AUTOMATIC_OUTCOME_ADMISSION",
            "AUTOMATIC_UNAVAILABLE_CLOSURE",
            "CONSTITUENT_AGGREGATION",
            "NETWORK_SOURCE_ACQUISITION",
            "SYNTHETIC_PORTFOLIO_NAV",
        ],
    }
    if next_due is not None:
        payload["launchd_template_fingerprint"] = _fingerprint(
            _launchd_template_payload(_expected_end_date(next_due))
        )
    payload["fingerprint"] = _fingerprint(payload)
    return payload


def write_prospective_outcome_due_schedule(
    *,
    artifact_path: Path,
    template_path: Path,
    schedule: Mapping[str, object],
) -> None:
    """Atomically write the audit and, if needed, one installable plist template."""
    _write_json_atomic(artifact_path, schedule)
    expected_date = schedule.get("next_due_date")
    if expected_date is None:
        template_path.unlink(missing_ok=True)
        return
    if not isinstance(expected_date, str):
        raise ProspectiveValidationError("next due date is malformed")
    _write_text_atomic(template_path, _render_launchd_template(date.fromisoformat(expected_date)))


def _live_pending_slots(store: ProspectiveValidationStore) -> list[dict[str, object]]:
    rows = store.rows(
        "SELECT slot.decision_id, slot.horizon_days, slot.expected_end_date, slot.status "
        "FROM prospective_outcome_slots AS slot "
        "JOIN prospective_decisions AS decision ON decision.decision_id = slot.decision_id "
        "WHERE decision.record_type = ? AND decision.lifecycle_status = ? AND slot.status = ? "
        "ORDER BY slot.expected_end_date, slot.decision_id, slot.horizon_days",
        (LIVE_RECORD, "FINALIZED", PENDING),
    )
    result: list[dict[str, object]] = []
    for row in rows:
        try:
            expected_end = date.fromisoformat(str(row["expected_end_date"]))
        except ValueError as error:
            raise ProspectiveValidationError("pending outcome slot has an invalid expected end date") from error
        result.append(
            {
                "decision_id": str(row["decision_id"]),
                "horizon_days": int(row["horizon_days"]),
                "expected_end_date": expected_end,
                "status": str(row["status"]),
            }
        )
    return result


def _expected_end_date(slot: Mapping[str, object]) -> date:
    value = slot.get("expected_end_date")
    if not isinstance(value, date):
        raise ProspectiveValidationError("pending outcome slot has an invalid expected end date")
    return value


def _launchd_template_payload(expected_date: object) -> dict[str, object]:
    if not isinstance(expected_date, date):
        raise ProspectiveValidationError("launchd template requires a calendar date")
    return {
        "label": LAUNCHD_JOB_IDENTIFIER,
        "run_date": expected_date.isoformat(),
        "run_time": SCHEDULED_LOCAL_TIME,
        "timezone": "Europe/Budapest",
        "working_directory": "__PROJECT_ROOT__",
        "command": MONITOR_COMMAND,
        "network_access": "NOT_USED",
    }


def _render_launchd_template(expected_date: date) -> str:
    """Render an installable template; project-root substitution is explicit and manual."""
    payload = _launchd_template_payload(expected_date)
    return """<?xml version=\"1.0\" encoding=\"UTF-8\"?>
<!DOCTYPE plist PUBLIC \"-//Apple//DTD PLIST 1.0//EN\" \"http://www.apple.com/DTDs/PropertyList-1.0.dtd\">
<plist version=\"1.0\">
<dict>
  <key>Label</key>
  <string>{label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>/bin/zsh</string>
    <string>-lc</string>
    <string>{command}</string>
  </array>
  <key>WorkingDirectory</key>
  <string>{working_directory}</string>
  <key>StartCalendarInterval</key>
  <dict>
    <key>Year</key><integer>{year}</integer>
    <key>Month</key><integer>{month}</integer>
    <key>Day</key><integer>{day}</integer>
    <key>Hour</key><integer>9</integer>
    <key>Minute</key><integer>0</integer>
  </dict>
  <key>StandardOutPath</key>
  <string>__PROJECT_ROOT__/data/audit/prospective_outcome_due_check.launchd.out.log</string>
  <key>StandardErrorPath</key>
  <string>__PROJECT_ROOT__/data/audit/prospective_outcome_due_check.launchd.err.log</string>
</dict>
</plist>
""".format(
        **{**payload, "command": str(payload["command"]).replace("&", "&amp;")},
        year=expected_date.year,
        month=expected_date.month,
        day=expected_date.day,
    )


def _write_json_atomic(path: Path, payload: Mapping[str, object]) -> None:
    _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=True) + "\n")


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(value)
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise
