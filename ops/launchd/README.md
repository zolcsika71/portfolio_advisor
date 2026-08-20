# Prospective outcome due-check template

`com.portfolio_advisor.prospective_outcome_due_check.plist` is a
ledger-derived launchd template. It contains `__PROJECT_ROOT__` intentionally;
the repository copy is never a machine-specific installed plist.

The scheduled command runs only the offline due monitor and prospective audit.
It does not acquire provider data, admit/close an outcome, or construct
portfolio performance.

Generate a rolling template from the next genuine live pending slot:

```bash
poetry run python scripts/schedule_prospective_outcome_due_checks.py
```

On macOS, an explicit user action can resolve the project root, validate the
plist, install it in the current user's `~/Library/LaunchAgents`, and enable
the single matching job:

```bash
poetry run python scripts/schedule_prospective_outcome_due_checks.py --install
```

Installation is idempotent for identical content and fails closed when a job
with the same label has different semantics. After a monitor run, regenerate
the next rolling schedule from the ledger.
