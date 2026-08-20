"""Focused fail-closed coverage for the event-driven XLS import watcher."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pytest

from portfolio_advisor.operations import xls_import_watch as watch
from portfolio_advisor.operations import xls_import_watch_installation as installation


def test_stable_xls_is_accepted(tmp_path: Path) -> None:
    workbook = tmp_path / "portfolio_20260801.xls"
    workbook.write_bytes(b"complete")

    assert watch.wait_for_stable_file(workbook, interval_seconds=0, retry_limit=1)


def test_zero_byte_xls_is_rejected(tmp_path: Path) -> None:
    workbook = tmp_path / "portfolio_20260801.xls"
    workbook.touch()

    assert not watch.wait_for_stable_file(workbook, interval_seconds=0, retry_limit=2)


def test_growing_or_retimestamped_xls_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workbook = tmp_path / "portfolio_20260801.xls"
    workbook.write_bytes(b"complete")
    snapshots = iter(
        [
            watch.FileSnapshot(10, 1), watch.FileSnapshot(11, 1),
            watch.FileSnapshot(11, 2), watch.FileSnapshot(11, 3),
        ]
    )
    monkeypatch.setattr(watch, "_snapshot", lambda _path: next(snapshots))

    assert not watch.wait_for_stable_file(workbook, interval_seconds=0, retry_limit=2)


def test_bounded_retry_can_succeed_on_a_later_stable_check(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    workbook = tmp_path / "portfolio_20260801.xls"
    workbook.write_bytes(b"complete")
    snapshots = iter(
        [
            watch.FileSnapshot(10, 1), watch.FileSnapshot(11, 1),
            watch.FileSnapshot(11, 2), watch.FileSnapshot(11, 2),
        ]
    )
    monkeypatch.setattr(watch, "_snapshot", lambda _path: next(snapshots))

    assert watch.wait_for_stable_file(workbook, interval_seconds=0, retry_limit=2)


def test_active_lock_prevents_second_process_and_releases_after_success(tmp_path: Path) -> None:
    path = tmp_path / "watch.lock"
    first = watch.ImportWatchLock(path)
    second = watch.ImportWatchLock(path)

    assert first.acquire()
    assert not second.acquire()
    first.release()
    assert second.acquire()
    second.release()
    assert not path.exists()


def test_stale_dead_lock_is_recovered_but_live_lock_is_not(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    path = tmp_path / "watch.lock"
    path.write_text(json.dumps({"pid": 999999, "created_at": time.time() - 100}), encoding="utf-8")
    monkeypatch.setattr(watch, "_pid_is_alive", lambda _pid: False)
    recovered = watch.ImportWatchLock(path, stale_age_seconds=1)
    assert recovered.acquire()
    recovered.release()

    path.write_text(json.dumps({"pid": os.getpid(), "created_at": time.time() - 100}), encoding="utf-8")
    monkeypatch.setattr(watch, "_pid_is_alive", lambda _pid: True)
    assert not watch.ImportWatchLock(path, stale_age_seconds=1).acquire()


def test_lock_is_released_after_importer_failure(tmp_path: Path) -> None:
    root = _root_with_database(tmp_path, ["2026-07-06"])
    workbook = root / "data/xls/import/portfolio_20260801.xls"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"complete")

    result = watch.run_watched_xls_import(
        repository_root=root,
        stability_interval_seconds=0,
        command_runner=lambda _command, _root: _completed(2, stderr="import failed"),
    )

    assert (result.status, result.error_stage) == ("IMPORT_FAILED", "IMPORTER")
    assert not (root / watch.DEFAULT_LOCK_PATH).exists()


def test_new_canonical_snapshot_runs_existing_post_import_commands(tmp_path: Path) -> None:
    root = _root_with_database(tmp_path, ["2026-07-06"])
    workbook = root / "data/xls/import/portfolio_20260801.xls"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"complete")
    commands: list[list[str]] = []

    def runner(command: list[str], _: Path) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        if command[-1] == "--import":
            _add_date(root / "database/model_portfolio.sqlite", "2026/08/01")
        return _completed(0, stdout="FINALIZED prospective decision")

    result = watch.run_watched_xls_import(
        repository_root=root, stability_interval_seconds=0, command_runner=runner
    )

    assert result.status == watch.SNAPSHOT_PROCESSED
    assert result.canonical_date == "2026-08-01"
    assert [command[1:] for command in commands] == [
        ["-m", "portfolio_advisor.main", "--import"],
        ["scripts/validate_active_ranking_policy_current_universe.py"],
        ["scripts/record_prospective_portfolio_decision.py", "--record-type", "live"],
        ["scripts/audit_prospective_portfolio_validation.py"],
    ]
    assert not (root / watch.DEFAULT_PENDING_PATH).exists()
    assert not (root / watch.DEFAULT_LOCK_PATH).exists()


def test_repeated_event_is_idempotent_and_no_file_is_a_noop(tmp_path: Path) -> None:
    root = _root_with_database(tmp_path, ["2026-07-06"])
    workbook = root / "data/xls/import/portfolio_20260706.xls"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"complete")
    called = False

    def runner(_command: list[str], _root: Path) -> subprocess.CompletedProcess[str]:
        nonlocal called
        called = True
        return _completed(0)

    repeated = watch.run_watched_xls_import(
        repository_root=root, stability_interval_seconds=0, command_runner=runner
    )
    workbook.unlink()
    no_file = watch.run_watched_xls_import(repository_root=root, stability_interval_seconds=0)

    assert repeated.status == watch.IMPORT_ALREADY_PROCESSED
    assert called  # The existing importer performs its own date-level skip.
    assert no_file.status == watch.NO_XLS_FILES


def test_old_historical_import_never_runs_live_decision_recording(tmp_path: Path) -> None:
    root = _root_with_database(tmp_path, ["2026-07-06"])
    workbook = root / "data/xls/import/portfolio_20260101.xls"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"complete")
    commands: list[list[str]] = []

    def runner(command: list[str], _: Path) -> subprocess.CompletedProcess[str]:
        commands.append(command)
        _add_date(root / "database/model_portfolio.sqlite", "2026/01/01")
        return _completed(0)

    result = watch.run_watched_xls_import(
        repository_root=root, stability_interval_seconds=0, command_runner=runner
    )

    assert result.status == watch.SNAPSHOT_PROCESSED
    assert len(commands) == 1
    assert "record_prospective" not in " ".join(commands[0])


def test_post_import_failure_leaves_a_retry_marker(tmp_path: Path) -> None:
    root = _root_with_database(tmp_path, ["2026-07-06"])
    workbook = root / "data/xls/import/portfolio_20260801.xls"
    workbook.parent.mkdir(parents=True)
    workbook.write_bytes(b"complete")

    def runner(command: list[str], _: Path) -> subprocess.CompletedProcess[str]:
        if command[-1] == "--import":
            _add_date(root / "database/model_portfolio.sqlite", "2026/08/01")
        if command[-1].endswith("validate_active_ranking_policy_current_universe.py"):
            return _completed(2, stderr="validation failed")
        return _completed(0)

    result = watch.run_watched_xls_import(
        repository_root=root, stability_interval_seconds=0, command_runner=runner
    )

    assert (result.status, result.error_stage) == ("POST_IMPORT_FAILED", "LATEST_UNIVERSE_VALIDATION")
    assert json.loads((root / watch.DEFAULT_PENDING_PATH).read_text(encoding="utf-8")) == {
        "decision_date": "2026-08-01"
    }


def test_watch_plist_is_exact_and_has_no_due_monitor_or_provider_action(tmp_path: Path) -> None:
    root = tmp_path / "portfolio_advisor"
    root.mkdir()
    content = installation.resolve_watch_plist(
        Path(__file__).resolve().parents[1] / installation.WATCH_TEMPLATE_PATH, root, Path(sys.executable)
    )
    payload = installation.plistlib.loads(content.encode("utf-8"))

    assert payload["WatchPaths"] == [str(root / "data/xls/import")]
    assert payload["ProgramArguments"] == [str(Path(sys.executable).absolute()), str(root / "scripts/process_watched_xls_import.py")]
    assert payload["WorkingDirectory"] == str(root)
    assert "check_due_prospective" not in content
    assert "provider" not in content.casefold()


def test_watch_plist_generation_is_deterministic_and_target_is_current_user_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / "portfolio_advisor"
    root.mkdir()
    template = Path(__file__).resolve().parents[1] / installation.WATCH_TEMPLATE_PATH
    first = installation.resolve_watch_plist(template, root, Path(sys.executable))
    second = installation.resolve_watch_plist(template, root, Path(sys.executable))
    monkeypatch.setattr(installation, "LAUNCH_AGENTS_DIRECTORY", tmp_path / "LaunchAgents")

    assert first == second
    with pytest.raises(installation.XlsImportWatchInstallationError):
        installation._validate_target(tmp_path / "LaunchDaemons" / installation.WATCH_TEMPLATE_NAME)


def test_identical_install_is_idempotent_and_conflict_fails_closed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    root = tmp_path / "portfolio_advisor"
    root.mkdir()
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    monkeypatch.setattr(installation, "LAUNCH_AGENTS_DIRECTORY", agents)
    target = agents / installation.WATCH_TEMPLATE_NAME
    template = Path(__file__).resolve().parents[1] / installation.WATCH_TEMPLATE_PATH
    content = installation.resolve_watch_plist(template, root, Path(sys.executable))
    target.write_text(content, encoding="utf-8")

    def loaded_runner(command: list[str]) -> subprocess.CompletedProcess[str]:
        if command[:2] == ["launchctl", "print"]:
            return _completed(0)
        if command[:2] == ["launchctl", "print-disabled"]:
            return _completed(0, stdout="disabled services = {}")
        return _completed(0)

    result = installation.install_xls_import_watch(
        template_path=template, repository_root=root, python_executable=Path(sys.executable), runner=loaded_runner
    )
    assert result.status == "ALREADY_INSTALLED_IDENTICAL"
    target.write_text(content.replace("process_watched_xls_import.py", "unexpected.py"), encoding="utf-8")
    with pytest.raises(installation.XlsImportWatchInstallationError):
        installation.install_xls_import_watch(
            template_path=template, repository_root=root, python_executable=Path(sys.executable), runner=loaded_runner
        )


def _root_with_database(tmp_path: Path, dates: list[str]) -> Path:
    root = tmp_path / "portfolio_advisor"
    root.mkdir()
    database = root / "database/model_portfolio.sqlite"
    database.parent.mkdir()
    with sqlite3.connect(database) as connection:
        connection.execute(
            'CREATE TABLE model_portfolios ('
            '"Date" TEXT, "Portfolio Name" TEXT, "Product" TEXT, "ISIN" TEXT, '
            '"Allocation (%)" REAL, "Asset Class" TEXT, "Currency" TEXT, "Currency Risk" TEXT, '
            '"1 Year" REAL, "1Y Sharpe Ratio" REAL, "1Y Volatility" REAL, '
            '"Downside Risk" REAL, "Maximum Drawdown" REAL)'
        )
        for value in dates:
            _add_date(connection, value.replace("-", "/"))
    return root


def _add_date(database: Path | sqlite3.Connection, value: str) -> None:
    if isinstance(database, Path):
        with sqlite3.connect(database) as connection:
            _add_date(connection, value)
        return
    database.execute('INSERT INTO model_portfolios ("Date") VALUES (?)', (value,))


def _completed(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)
