"""Shared deterministic JSON serialization and SHA-256 fingerprinting."""

from __future__ import annotations

import json
from hashlib import sha256


def canonical_json(value: object) -> str:
    """Serialize JSON-compatible data without environment- or order-dependent bytes."""
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"))


def canonical_fingerprint(value: object) -> str:
    """Fingerprint the repository's canonical JSON representation."""
    return sha256(canonical_json(value).encode("utf-8")).hexdigest()
