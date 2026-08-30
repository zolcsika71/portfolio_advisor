"""Print the deterministic, read-only objective-policy registry audit."""

from __future__ import annotations

import sys

from portfolio_advisor.objectives import (
    ObjectiveFrameworkError,
    build_default_policy_registry,
    render_registry_audit,
)


def main() -> int:
    """Validate authoritative artifacts and emit canonical JSON to stdout."""
    try:
        output = render_registry_audit(build_default_policy_registry())
    except (ObjectiveFrameworkError, OSError, ValueError, RuntimeError) as error:
        print(f"Objective policy registry audit failed: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
