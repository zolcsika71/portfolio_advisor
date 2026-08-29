"""Verified SQLite backups for a future, explicitly authorized migration."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


def create_verified_backup(source: Path, destination: Path) -> Path:
    """Copy one SQLite file and verify byte-level SHA-256 equality.

    This helper is intentionally not called by Milestone 5 schema scaffolding.
    It refuses to replace an existing destination.
    """
    if not source.is_file() or source.is_symlink():
        raise ValueError("backup source must be a regular SQLite file")
    if destination.exists():
        raise ValueError("backup destination already exists")
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copyfile(source, destination)
        if _sha256(source) != _sha256(destination):
            raise RuntimeError("verified backup SHA-256 mismatch")
    except BaseException:
        if destination.exists():
            destination.unlink()
        raise
    return destination


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()
