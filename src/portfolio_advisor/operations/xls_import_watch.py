"""Fail-closed orchestration for the launchd XLS import watcher.

This module deliberately delegates workbook parsing to the existing importer
and decision finalization to the existing scripts.  It adds only filesystem
stability, single-process locking, sequencing, and concise local logging.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

from portfolio_advisor.database.repository import (
    ModelPortfolioRepository,
    RepositoryError,
)

DEFAULT_INPUT_DIRECTORY: Final = Path("data/xls/import")
DEFAULT_LOCK_PATH: Final = Path("tmp/xls_import_watch.lock")
DEFAULT_PENDING_PATH: Final = Path("tmp/xls_import_watch_pending_snapshot.json")
DEFAULT_LOG_PATH: Final = Path("logs/xls_import_watch.log")
STABILITY_CHECK_INTERVAL_SECONDS: Final = 3.0
STABILITY_RETRY_LIMIT: Final = 2
STALE_LOCK_AGE_SECONDS: Final = 30 * 60

NO_XLS_FILES: Final = "NO_XLS_FILES"
XLS_FILE_NOT_STABLE: Final = "XLS_FILE_NOT_STABLE"
IMPORT_ALREADY_PROCESSED: Final = "IMPORT_ALREADY_PROCESSED"
SNAPSHOT_PROCESSED: Final = "SNAPSHOT_PROCESSED"
PROSPECTIVE_DECISION_ALREADY_RECORDED: Final = "PROSPECTIVE_DECISION_ALREADY_RECORDED"


@dataclass(frozen=True, slots=True)
class WatchRunResult:
    """One concise watcher invocation outcome."""

    status: str
    exit_code: int
    error_stage: str | None = None
    canonical_date: str | None = None


@dataclass(frozen=True, slots=True)
class FileSnapshot:
    """The only file attributes used by the copy-completion guard."""

    size: int
    mtime_ns: int


def candidate_xls_files(input_directory: Path) -> tuple[Path, ...]:
    """Return supported direct children in deterministic order."""
    if not input_directory.is_dir():
        return ()
    return tuple(
        sorted(
            (path for path in input_directory.iterdir() if path.is_file() and path.suffix.casefold() == ".xls"),
            key=lambda path: path.name.casefold(),
        )
    )


def wait_for_stable_file(
    path: Path,
    *,
    interval_seconds: float = STABILITY_CHECK_INTERVAL_SECONDS,
    retry_limit: int = STABILITY_RETRY_LIMIT,
    sleeper: Callable[[float], None] = time.sleep,
) -> bool:
    """Require a positive-size XLS to retain size and mtime over a bounded window."""
    if retry_limit < 1:
        raise ValueError("stability retry limit must be at least one")
    if interval_seconds < 0:
        raise ValueError("stability check interval cannot be negative")
    if path.suffix.casefold() != ".xls":
        return False
    for _ in range(retry_limit):
        before = _snapshot(path)
        if before is None or before.size <= 0:
            return False
        sleeper(interval_seconds)
        after = _snapshot(path)
        if after is not None and after.size > 0 and after == before:
            return True
    return False


def _snapshot(path: Path) -> FileSnapshot | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    if not path.is_file():
        return None
    return FileSnapshot(size=stat.st_size, mtime_ns=stat.st_mtime_ns)


class ImportWatchLock:
    """Atomic local lock; stale recovery is allowed only for a dead old PID."""

    def __init__(self, path: Path, *, stale_age_seconds: int = STALE_LOCK_AGE_SECONDS) -> None:
        self.path = path
        self.stale_age_seconds = stale_age_seconds
        self.acquired = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        for _ in range(2):
            try:
                descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            except FileExistsError:
                if not self._recover_stale_lock():
                    return False
                continue
            with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
                json.dump({"pid": os.getpid(), "created_at": time.time()}, handle, sort_keys=True)
                handle.write("\n")
            self.acquired = True
            return True
        return False

    def release(self) -> None:
        if self.acquired:
            self.path.unlink(missing_ok=True)
            self.acquired = False

    def _recover_stale_lock(self) -> bool:
        try:
            payload = json.loads(self.path.read_text(encoding="utf-8"))
            pid = int(payload["pid"])
            created_at = float(payload["created_at"])
        except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
            return False
        if time.time() - created_at < self.stale_age_seconds or _pid_is_alive(pid):
            return False
        try:
            self.path.unlink()
        except FileNotFoundError:
            return True
        return True


def _pid_is_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def run_watched_xls_import(
    *,
    repository_root: Path,
    dry_run: bool = False,
    stability_interval_seconds: float = STABILITY_CHECK_INTERVAL_SECONDS,
    stability_retry_limit: int = STABILITY_RETRY_LIMIT,
    sleeper: Callable[[float], None] = time.sleep,
    command_runner: Callable[[list[str], Path], subprocess.CompletedProcess[str]] | None = None,
    lock_path: Path | None = None,
    pending_path: Path | None = None,
    log_path: Path | None = None,
) -> WatchRunResult:
    """Process one launchd event without importing incomplete workbooks.

    A pending marker is written only after this watcher adds a new latest
    canonical snapshot.  It makes an interrupted post-import finalization
    safely retryable without turning an arbitrary pre-existing snapshot into
    live evidence.
    """
    root = repository_root.resolve()
    input_directory = root / DEFAULT_INPUT_DIRECTORY
    lock = ImportWatchLock(lock_path or root / DEFAULT_LOCK_PATH)
    pending = pending_path or root / DEFAULT_PENDING_PATH
    logger = _OperationalLog(log_path or root / DEFAULT_LOG_PATH)
    files = candidate_xls_files(input_directory)
    logger.write("event", files=[path.name for path in files], dry_run=dry_run)

    if files and not all(
        wait_for_stable_file(
            path,
            interval_seconds=stability_interval_seconds,
            retry_limit=stability_retry_limit,
            sleeper=sleeper,
        )
        for path in files
    ):
        logger.write("stability", status=XLS_FILE_NOT_STABLE)
        return WatchRunResult(XLS_FILE_NOT_STABLE, 0)
    if not files and not pending.is_file():
        logger.write("complete", status=NO_XLS_FILES)
        return WatchRunResult(NO_XLS_FILES, 0)
    if not lock.acquire():
        logger.write("lock", status="IMPORT_LOCK_ACTIVE")
        return WatchRunResult("IMPORT_LOCK_ACTIVE", 0)
    try:
        if dry_run:
            logger.write("complete", status="DRY_RUN_READY")
            return WatchRunResult("DRY_RUN_READY", 0)
        before_dates = _observation_dates(root)
        if files:
            importer = _run(
                [sys.executable, "-m", "portfolio_advisor.main", "--import"], root, command_runner
            )
            logger.write("import", returncode=importer.returncode, output=_concise_output(importer))
            if importer.returncode != 0:
                return WatchRunResult("IMPORT_FAILED", 2, "IMPORTER")
        after_dates = _observation_dates(root)
        imported_dates = sorted(after_dates - before_dates)
        current_date = max(after_dates) if after_dates else None
        canonical_date: str | None = None
        if current_date is not None and current_date in imported_dates:
            canonical_date = current_date
            _write_pending_snapshot(pending, canonical_date)
            logger.write("canonical_snapshot", date=canonical_date)
        else:
            marker_date = _read_pending_snapshot(pending)
            if marker_date is not None and marker_date == current_date:
                canonical_date = marker_date
                logger.write("pending_retry", date=canonical_date)
            elif marker_date is not None:
                pending.unlink(missing_ok=True)
                logger.write("pending_retry_discarded", marker_date=marker_date, latest_date=current_date)
        if canonical_date is None:
            status = IMPORT_ALREADY_PROCESSED if files and not imported_dates else SNAPSHOT_PROCESSED
            logger.write("complete", status=status, imported_dates=imported_dates)
            return WatchRunResult(status, 0)
        result = _run_post_import_workflow(root, command_runner, logger)
        if result is None:
            pending.unlink(missing_ok=True)
            status = (
                PROSPECTIVE_DECISION_ALREADY_RECORDED
                if _last_record_was_identical(logger)
                else SNAPSHOT_PROCESSED
            )
            logger.write("complete", status=status, canonical_date=canonical_date)
            return WatchRunResult(status, 0, canonical_date=canonical_date)
        return WatchRunResult("POST_IMPORT_FAILED", 2, result, canonical_date)
    except (OSError, RepositoryError, ValueError) as error:
        logger.write("error", stage="ORCHESTRATION", message=str(error))
        return WatchRunResult("ORCHESTRATION_FAILED", 2, "ORCHESTRATION")
    finally:
        lock.release()


def _run_post_import_workflow(
    root: Path,
    command_runner: Callable[[list[str], Path], subprocess.CompletedProcess[str]] | None,
    logger: _OperationalLog,
) -> str | None:
    commands = (
        ("LATEST_UNIVERSE_VALIDATION", [sys.executable, "scripts/validate_active_ranking_policy_current_universe.py"]),
        ("PROSPECTIVE_DECISION", [sys.executable, "scripts/record_prospective_portfolio_decision.py", "--record-type", "live"]),
        ("PROSPECTIVE_AUDIT", [sys.executable, "scripts/audit_prospective_portfolio_validation.py"]),
    )
    for stage, command in commands:
        completed = _run(command, root, command_runner)
        output = _concise_output(completed)
        logger.write("post_import", stage=stage, returncode=completed.returncode, output=output)
        if stage == "PROSPECTIVE_DECISION":
            logger.last_record_identical = "ALREADY_RECORDED_IDENTICAL" in output
        if completed.returncode != 0:
            return stage
    return None


def _run(
    command: list[str],
    root: Path,
    command_runner: Callable[[list[str], Path], subprocess.CompletedProcess[str]] | None,
) -> subprocess.CompletedProcess[str]:
    if command_runner is not None:
        return command_runner(command, root)
    return subprocess.run(command, cwd=root, check=False, capture_output=True, text=True)


def _observation_dates(root: Path) -> set[str]:
    database_path = root / "database/model_portfolio.sqlite"
    if not database_path.is_file():
        return set()
    return {item.isoformat() for item in ModelPortfolioRepository(database_path).observation_dates()}


def _write_pending_snapshot(path: Path, decision_date: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump({"decision_date": decision_date}, handle, sort_keys=True)
            handle.write("\n")
        Path(temporary).replace(path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _read_pending_snapshot(path: Path) -> str | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8")).get("decision_date")
    except (OSError, ValueError, AttributeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, str) else None


def _concise_output(completed: subprocess.CompletedProcess[str]) -> str:
    value = (completed.stdout or completed.stderr or "").strip().replace("\n", " | ")
    return value[:500]


def _last_record_was_identical(logger: _OperationalLog) -> bool:
    return logger.last_record_identical


class _OperationalLog:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.last_record_identical = False

    def write(self, event: str, **values: object) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "timestamp": datetime.now(UTC).isoformat(),
            "event": event,
            **values,
        }
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, sort_keys=True) + "\n")
