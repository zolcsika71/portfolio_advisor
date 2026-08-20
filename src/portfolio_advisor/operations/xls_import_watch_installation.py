"""Explicit current-user installation of the XLS WatchPaths LaunchAgent."""

from __future__ import annotations

import hashlib
import json
import os
import plistlib
import re
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

WATCH_JOB_IDENTIFIER: Final = "com.portfolio_advisor.xls_import_watch"
WATCH_TEMPLATE_NAME: Final = f"{WATCH_JOB_IDENTIFIER}.plist"
WATCH_TEMPLATE_PATH: Final = Path("ops/launchd") / WATCH_TEMPLATE_NAME
LAUNCH_AGENTS_DIRECTORY: Final = Path.home() / "Library" / "LaunchAgents"
PROJECT_ROOT_MARKER: Final = "__PROJECT_ROOT__"
PYTHON_EXECUTABLE_MARKER: Final = "__PYTHON_EXECUTABLE__"


class XlsImportWatchInstallationError(RuntimeError):
    """A template or current-user launchd safety check failed."""


@dataclass(frozen=True, slots=True)
class XlsImportWatchInstallationResult:
    status: str
    job_identifier: str
    installed_plist_path: Path
    plist_fingerprint: str
    loaded: bool
    enabled: bool


def resolve_watch_plist(template_path: Path, repository_root: Path, python_executable: Path) -> str:
    """Resolve and semantically validate a machine-specific LaunchAgent plist."""
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as error:
        raise XlsImportWatchInstallationError("XLS import watch template is missing or unreadable") from error
    root = repository_root.resolve()
    # Keep the virtualenv symlink itself.  Resolving it would make launchd run
    # the underlying base interpreter, which need not contain this project.
    python_path = python_executable.expanduser().absolute()
    if not root.is_absolute() or not python_path.is_absolute() or not python_path.is_file():
        raise XlsImportWatchInstallationError("repository root and Poetry environment Python must be absolute existing paths")
    content = template.replace(PROJECT_ROOT_MARKER, str(root)).replace(PYTHON_EXECUTABLE_MARKER, str(python_path))
    if PROJECT_ROOT_MARKER in content or PYTHON_EXECUTABLE_MARKER in content:
        raise XlsImportWatchInstallationError("launchd template placeholder resolution failed")
    _validate_payload(_load_plist(content), root, python_path)
    return content


def validate_watch_plist(content: str, repository_root: Path, python_executable: Path) -> str:
    """Return the deterministic semantic fingerprint of an installed plist."""
    payload = _load_plist(content)
    _validate_payload(payload, repository_root.resolve(), python_executable.expanduser().absolute())
    return _fingerprint(payload)


def install_xls_import_watch(
    *,
    template_path: Path,
    repository_root: Path,
    python_executable: Path,
    target_path: Path | None = None,
    user_id: int | None = None,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> XlsImportWatchInstallationResult:
    """Install precisely one validated current-user watcher, never overwrite it."""
    target = target_path or LAUNCH_AGENTS_DIRECTORY / WATCH_TEMPLATE_NAME
    _validate_target(target)
    content = resolve_watch_plist(template_path, repository_root, python_executable)
    fingerprint = validate_watch_plist(content, repository_root, python_executable)
    uid = os.getuid() if user_id is None else user_id
    execute = runner or _run
    loaded_before = _is_loaded(execute, uid)
    enabled_before = _is_enabled(execute, uid)
    if target.exists() and not target.is_file():
        raise XlsImportWatchInstallationError("launchd target exists but is not a regular plist file")
    if target.is_file():
        existing_fingerprint = validate_watch_plist(target.read_text(encoding="utf-8"), repository_root, python_executable)
        if existing_fingerprint != fingerprint:
            raise XlsImportWatchInstallationError(
                "installed XLS import WatchAgent differs from the validated template; refusing replacement"
            )
        if loaded_before and enabled_before:
            return XlsImportWatchInstallationResult(
                "ALREADY_INSTALLED_IDENTICAL", WATCH_JOB_IDENTIFIER, target, fingerprint, True, True
            )
    elif loaded_before:
        raise XlsImportWatchInstallationError("matching launchd job is loaded without the expected current-user plist")
    else:
        _write_validated_plist(target, content, execute)
    _require_success(execute(["launchctl", "bootstrap", f"gui/{uid}", str(target)]), "launchctl bootstrap")
    _require_success(execute(["launchctl", "enable", f"gui/{uid}/{WATCH_JOB_IDENTIFIER}"]), "launchctl enable")
    if not _is_loaded(execute, uid) or not _is_enabled(execute, uid):
        raise XlsImportWatchInstallationError("launchd did not load and enable exactly the XLS import watcher")
    return XlsImportWatchInstallationResult(
        "INSTALLED_AND_ENABLED", WATCH_JOB_IDENTIFIER, target, fingerprint, True, True
    )


def _load_plist(content: str) -> dict[str, object]:
    try:
        payload = plistlib.loads(content.encode("utf-8"))
    except (ValueError, plistlib.InvalidFileException) as error:
        raise XlsImportWatchInstallationError("XLS import watch plist is malformed") from error
    if not isinstance(payload, dict):
        raise XlsImportWatchInstallationError("XLS import watch plist must contain a root dictionary")
    return payload


def _validate_payload(payload: dict[str, object], root: Path, python_path: Path) -> None:
    expected_command = [str(python_path), str(root / "scripts/process_watched_xls_import.py")]
    if payload.get("Label") != WATCH_JOB_IDENTIFIER:
        raise XlsImportWatchInstallationError("launchd plist label differs from the XLS import watcher")
    if payload.get("ProgramArguments") != expected_command:
        raise XlsImportWatchInstallationError("launchd plist must invoke only the XLS import wrapper")
    if payload.get("WorkingDirectory") != str(root):
        raise XlsImportWatchInstallationError("launchd plist working directory differs from the repository root")
    if payload.get("WatchPaths") != [str(root / "data/xls/import")]:
        raise XlsImportWatchInstallationError("launchd plist WatchPaths differs from the XLS import directory")
    blocked = ("acquire", "provider", "network", "admit_prospective", "check_due_prospective")
    serialized = json.dumps(payload, sort_keys=True).casefold()
    if any(value in serialized for value in blocked):
        raise XlsImportWatchInstallationError("launchd plist includes a prohibited operational action")


def _fingerprint(payload: dict[str, object]) -> str:
    return hashlib.sha256(json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def _validate_target(target: Path) -> None:
    expected = LAUNCH_AGENTS_DIRECTORY / WATCH_TEMPLATE_NAME
    if target != expected:
        raise XlsImportWatchInstallationError("launchd installation target is not the exact current-user XLS watch plist")


def _write_validated_plist(
    target: Path, content: str, runner: Callable[[list[str]], subprocess.CompletedProcess[str]]
) -> None:
    target.parent.mkdir(parents=False, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent, text=True)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
        _require_success(runner(["plutil", "-lint", temporary]), "plutil validation")
        Path(temporary).chmod(0o644)
        try:
            os.link(temporary, target)
        except FileExistsError as error:
            raise XlsImportWatchInstallationError("launchd target appeared during installation; refusing replacement") from error
        Path(temporary).unlink(missing_ok=True)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _is_loaded(runner: Callable[[list[str]], subprocess.CompletedProcess[str]], user_id: int) -> bool:
    return runner(["launchctl", "print", f"gui/{user_id}/{WATCH_JOB_IDENTIFIER}"]).returncode == 0


def _is_enabled(runner: Callable[[list[str]], subprocess.CompletedProcess[str]], user_id: int) -> bool:
    result = runner(["launchctl", "print-disabled", f"gui/{user_id}"])
    if result.returncode != 0:
        raise XlsImportWatchInstallationError("launchctl print-disabled failed while verifying watcher state")
    pattern = rf'["\']?{re.escape(WATCH_JOB_IDENTIFIER)}["\']?\s*(?:=>|=)\s*true'
    return re.search(pattern, result.stdout + result.stderr, flags=re.IGNORECASE) is None


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=False, capture_output=True, text=True)


def _require_success(result: subprocess.CompletedProcess[str], action: str) -> None:
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise XlsImportWatchInstallationError(f"{action} failed: {message}")
