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

## XLS import watcher

`com.portfolio_advisor.xls_import_watch.plist` is a separate current-user
`WatchPaths` template. It watches only `data/xls/import` and invokes only the
project wrapper. The installer resolves the project root and the Python path
from the active Poetry environment; it does not use an interactive shell.

```bash
poetry run python scripts/process_watched_xls_import.py --dry-run
poetry run python scripts/install_xls_import_watch.py --dry-run
poetry run python scripts/install_xls_import_watch.py --install
```

The watcher never fetches a provider, admits an outcome, or changes the
separate prospective due-monitor LaunchAgent.

It accepts a workbook only after its positive size and nanosecond mtime match
across one 3-second stability interval (up to two attempts). A local atomic
lock prevents concurrent SQLite imports; a lock can be recovered only after
30 minutes when its recorded PID is no longer alive. Normal outcomes are
written as JSON lines to `logs/xls_import_watch.log`.
