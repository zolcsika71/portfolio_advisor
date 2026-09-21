"""Immutable local retention for screenshot-backed LTIA evidence."""

from __future__ import annotations

import hashlib
import os
import shutil
import tempfile
from pathlib import Path

from .models import (
    ScreenshotArtifactInput,
    ScreenshotArtifactRole,
    SourceConflictError,
    TbszError,
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"


def retain_screenshot_artifact(
    source_path: Path,
    *,
    evidence_root: Path,
    role: ScreenshotArtifactRole,
    expected_sha256: str,
) -> ScreenshotArtifactInput:
    """Copy exact PNG bytes into content-addressed local evidence storage."""
    if not evidence_root.is_dir() or evidence_root.is_symlink():
        raise TbszError("screenshot evidence root must be an existing non-symlink directory")
    if source_path.is_symlink() or not source_path.is_file():
        raise TbszError("screenshot source must be an existing non-symlink file")
    if Path(source_path.name).name != source_path.name or not source_path.name.casefold().endswith(".png"):
        raise TbszError("screenshot source must have a plain PNG filename")
    source_hash = _sha256(source_path)
    if source_hash != expected_sha256:
        raise SourceConflictError("screenshot source hash differs from the authorized hash")
    byte_count = source_path.stat().st_size
    with source_path.open("rb") as source:
        if source.read(8) != _PNG_SIGNATURE:
            raise TbszError("screenshot source is not a PNG byte stream")

    evidence_directory = evidence_root / "source" / "screenshots"
    _ensure_local_directory(evidence_root, evidence_directory)
    retained_relative = Path("source") / "screenshots" / f"{source_hash}.png"
    retained_path = evidence_root / retained_relative
    if retained_path.exists():
        _verify_existing(retained_path, source_hash=source_hash, byte_count=byte_count)
    else:
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{source_hash}.", suffix=".tmp", dir=evidence_directory
        )
        temporary_path = Path(temporary_name)
        try:
            with os.fdopen(descriptor, "wb") as destination, source_path.open("rb") as source:
                shutil.copyfileobj(source, destination)
                destination.flush()
                os.fsync(destination.fileno())
            _verify_existing(temporary_path, source_hash=source_hash, byte_count=byte_count)
            try:
                os.link(temporary_path, retained_path)
            except FileExistsError:
                _verify_existing(retained_path, source_hash=source_hash, byte_count=byte_count)
        finally:
            temporary_path.unlink(missing_ok=True)
    return ScreenshotArtifactInput(
        role=role,
        source_filename=source_path.name,
        retained_path=retained_relative.as_posix(),
        content_sha256=source_hash,
        byte_count=byte_count,
    )


def _ensure_local_directory(evidence_root: Path, destination: Path) -> None:
    root = evidence_root.resolve(strict=True)
    relative_destination = destination.relative_to(evidence_root)
    current = evidence_root
    for part in relative_destination.parts:
        current /= part
        if current.exists() and current.is_symlink():
            raise TbszError("screenshot evidence path contains a symlink")
    destination.mkdir(parents=True, exist_ok=True)
    if destination.is_symlink():
        raise TbszError("screenshot evidence directory must not be a symlink")
    try:
        destination.resolve(strict=True).relative_to(root)
    except ValueError as error:
        raise TbszError("screenshot evidence directory escapes the evidence root") from error


def _verify_existing(path: Path, *, source_hash: str, byte_count: int) -> None:
    if path.is_symlink() or not path.is_file():
        raise TbszError("retained screenshot target must be a non-symlink file")
    if path.stat().st_size != byte_count or _sha256(path) != source_hash:
        raise SourceConflictError("retained screenshot target conflicts with authorized bytes")
    with path.open("rb") as source:
        if source.read(8) != _PNG_SIGNATURE:
            raise TbszError("retained screenshot target is not a PNG byte stream")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        while block := source.read(1024 * 1024):
            digest.update(block)
    return digest.hexdigest()
