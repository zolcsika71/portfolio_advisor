"""Explicit, idempotent current-user launchd installation for the due-check template."""

from __future__ import annotations

import os
import plistlib
import subprocess
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from .due_scheduling import LAUNCHD_JOB_IDENTIFIER
from .validation import ProspectiveValidationError, _fingerprint

LAUNCH_AGENTS_DIRECTORY: Final = Path.home() / "Library" / "LaunchAgents"
INSTALLABLE_TEMPLATE_MARKER: Final = "__PROJECT_ROOT__"


@dataclass(frozen=True, slots=True)
class LaunchdInstallationResult:
    status: str
    job_identifier: str
    installed_plist_path: Path
    plist_fingerprint: str
    loaded: bool
    enabled: bool


def install_prospective_due_schedule(
    *,
    template_path: Path,
    repository_root: Path,
    target_path: Path | None = None,
    user_id: int | None = None,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]] | None = None,
) -> LaunchdInstallationResult:
    """Install only this current-user template, refusing any unexpected replacement."""
    target = target_path or LAUNCH_AGENTS_DIRECTORY / f"{LAUNCHD_JOB_IDENTIFIER}.plist"
    _validate_target(target)
    content = _resolved_plist_content(template_path, repository_root)
    fingerprint = _plist_fingerprint(content)
    uid = os.getuid() if user_id is None else user_id
    execute = runner or _run
    target_exists = target.is_file()
    loaded_before = _is_loaded(execute, uid)
    if target.exists() and not target_exists:
        raise ProspectiveValidationError("launchd target exists but is not a regular plist file")
    if target_exists:
        existing = target.read_text(encoding="utf-8")
        installed_fingerprint = _plist_fingerprint(existing)
        if installed_fingerprint != fingerprint:
            raise ProspectiveValidationError(
                "installed LaunchAgent differs from the validated template; refusing replacement "
                f"(installed={installed_fingerprint}, expected={fingerprint})"
            )
        if loaded_before:
            return LaunchdInstallationResult(
                status="ALREADY_INSTALLED_IDENTICAL",
                job_identifier=LAUNCHD_JOB_IDENTIFIER,
                installed_plist_path=target,
                plist_fingerprint=fingerprint,
                loaded=True,
                enabled=True,
            )
    elif loaded_before:
        raise ProspectiveValidationError("matching launchd job is loaded without the expected current-user plist")
    else:
        _write_validated_plist(target, content, execute)
    _require_success(execute(["launchctl", "bootstrap", f"gui/{uid}", str(target)]), "launchctl bootstrap")
    _require_success(execute(["launchctl", "enable", f"gui/{uid}/{LAUNCHD_JOB_IDENTIFIER}"]), "launchctl enable")
    if not _is_loaded(execute, uid):
        raise ProspectiveValidationError("launchd did not load the due-check job after bootstrap")
    return LaunchdInstallationResult(
        status="INSTALLED_AND_ENABLED",
        job_identifier=LAUNCHD_JOB_IDENTIFIER,
        installed_plist_path=target,
        plist_fingerprint=fingerprint,
        loaded=True,
        enabled=True,
    )


def _resolved_plist_content(template_path: Path, repository_root: Path) -> str:
    try:
        template = template_path.read_text(encoding="utf-8")
    except OSError as error:
        raise ProspectiveValidationError("launchd template is missing or unreadable") from error
    root = str(repository_root.resolve())
    if not root.startswith("/"):
        raise ProspectiveValidationError("repository root must be an absolute path for launchd")
    if template.count(INSTALLABLE_TEMPLATE_MARKER) != 3:
        raise ProspectiveValidationError("launchd template has an unexpected project-root placeholder count")
    content = template.replace(INSTALLABLE_TEMPLATE_MARKER, root)
    if INSTALLABLE_TEMPLATE_MARKER in content:
        raise ProspectiveValidationError("launchd template placeholder resolution failed")
    required = (
        f"<string>{LAUNCHD_JOB_IDENTIFIER}</string>",
        "scripts/check_due_prospective_outcomes.py",
        "scripts/audit_prospective_portfolio_validation.py",
    )
    if any(item not in content for item in required):
        raise ProspectiveValidationError("launchd template command semantics differ from the validated schedule")
    blocked = ("acquire", "admit_prospective", "assess_due_prospective", "SYNTHETIC_PORTFOLIO_NAV")
    if any(item in content for item in blocked):
        raise ProspectiveValidationError("launchd template includes a prohibited operational action")
    return content


def _plist_fingerprint(content: str) -> str:
    try:
        payload = plistlib.loads(content.encode("utf-8"))
    except (ValueError, plistlib.InvalidFileException) as error:
        raise ProspectiveValidationError("launchd plist is malformed") from error
    if not isinstance(payload, dict):
        raise ProspectiveValidationError("launchd plist must contain a root dictionary")
    return _fingerprint(payload)


def _validate_target(target: Path) -> None:
    expected = LAUNCH_AGENTS_DIRECTORY / f"{LAUNCHD_JOB_IDENTIFIER}.plist"
    if target != expected:
        raise ProspectiveValidationError("launchd installation target is not the exact current-user due-check plist")


def _write_validated_plist(
    target: Path,
    content: str,
    runner: Callable[[list[str]], subprocess.CompletedProcess[str]],
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
            raise ProspectiveValidationError("launchd target appeared during installation; refusing replacement") from error
        Path(temporary).unlink(missing_ok=True)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _is_loaded(runner: Callable[[list[str]], subprocess.CompletedProcess[str]], user_id: int) -> bool:
    return runner(["launchctl", "print", f"gui/{user_id}/{LAUNCHD_JOB_IDENTIFIER}"]).returncode == 0


def _run(arguments: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=False, capture_output=True, text=True)


def _require_success(result: subprocess.CompletedProcess[str], action: str) -> None:
    if result.returncode != 0:
        message = result.stderr.strip() or result.stdout.strip() or "no diagnostic output"
        raise ProspectiveValidationError(f"{action} failed: {message}")
