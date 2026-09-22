"""Deterministic policy-repository inputs for registry and construction tests."""

from __future__ import annotations

import json
from pathlib import Path

from portfolio_advisor.objectives import (
    CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    CAPITAL_POLICY_ARTIFACT,
)
from portfolio_advisor.objectives.registry import (
    CAPITAL_METHODOLOGY_ARTIFACT,
    CAPITAL_STRICT_PIPELINE_ARTIFACT,
)


def create_policy_registry_repository(path: Path, source_root: Path) -> Path:
    """Create the smallest repository tree accepted by the real registry validators.

    The reviewed policy YAML files are copied byte-for-byte so their governed
    fingerprints remain meaningful.  The generated JSON documents contain only
    the validation fields consumed by ``build_policy_contract``.
    """
    root = path / "synthetic-policy-repository"
    _copy_reviewed_policy(source_root, root, CAPITAL_POLICY_ARTIFACT)
    _copy_reviewed_policy(
        source_root,
        root,
        CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY_ARTIFACT,
    )
    _write_json(
        root / CAPITAL_METHODOLOGY_ARTIFACT,
        {
            "caveats": ["Synthetic fixture; not production validation evidence."],
            "look_ahead_validation": {"result": "PASS"},
            "monotonicity": {
                "capital_preservation_dominance": "PASS",
                "catastrophic_drawdown_high_return": {"result": "PASS"},
                "drawdown_direction": "PASS",
                "smooth_persistent_loss": {"result": "PASS"},
            },
            "validation_status": (
                "CAPITAL_PRESERVATION_METHODOLOGY_VALIDATED_WITH_CAVEATS"
            ),
        },
    )
    _write_json(
        root / CAPITAL_STRICT_PIPELINE_ARTIFACT,
        {"validation_status": "STRICT_BACKTEST_PIPELINE_VALIDATED"},
    )
    return root


def _copy_reviewed_policy(
    source_root: Path, target_root: Path, relative_path: str
) -> None:
    source = source_root / relative_path
    target = target_root / relative_path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(source.read_bytes())


def _write_json(path: Path, value: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
