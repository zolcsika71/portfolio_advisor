# Agents

## Current implementation status

This repository has no LLM or autonomous software agents. It now includes a
deterministic advisor workflow, with all financial calculations and ranking
performed by typed Python functions and reviewed configuration.

`src/portfolio_advisor/main.py` emits the structured capital-preservation
analysis result by default. `--import` retains the existing import workflow.

## Implemented application components (not agents)

Although there are no agents yet, the repository does contain a data-import
pipeline that future agents may call or coordinate:

### Excel processing layer

`src/portfolio_advisor/DB_creation/excel_processing.py` reads visible
`Modell portfóliók` worksheets, fills merged cells, translates Hungarian and
English headers and categorical values, validates the resulting columns, and
normalizes values for database insertion.

Relevant entry points include:

- `read_target_worksheet()` — loads the supported worksheet from a workbook.
- `translate_headers()` — maps worksheet headers to the stable English schema.
- `translate_values()` — maps supported categorical values and rejects unknown
  categories.
- `prepare_rows()` — prepares validated rows for import.
- `add_date_field()` — adds one validated `Date` value to every normalized row.

Only the visible `modell portfóliók` worksheet is accepted. Headers and
supported categorical values are translated systematically into English.

### SQLite import layer

`src/portfolio_advisor/DB_creation/database_create.py` imports prepared workbook
rows into SQLite. It extracts an import date from the filename, creates or
validates the `model_portfolios` table, skips dates already present, and wraps
each import in a commit/rollback context.

Relevant entry points include:

- `extract_date()` — converts an eight-digit filename date to `YYYY/MM/DD`.
- `import_file()` — imports one supported workbook atomically.
- `DatabaseSession` — owns SQLite connection and transaction lifecycle.
- `ensure_data_table()` — creates or validates the expected schema without
  silently altering an incompatible table.
- `process_directory()` — finds all `.xls` files, ensures the database exists,
  imports them in filename order, and moves successful files to the processed
  directory.
- `main()` — exposes the command-line importer.

Default paths are:

- Input: `/Users/zoltanka/Documents/Prog/Python/portfolio_advisor/data/xls/import`
- Processed: `/Users/zoltanka/Documents/Prog/Python/portfolio_advisor/data/xls/processed`
- Database: `/Users/zoltanka/Documents/Prog/Python/portfolio_advisor/database/model_portfolio.sqlite`

The SQLite table is `model_portfolios`. It contains `Date`, translated
portfolio fields, allocation values, and portfolio risk/performance metrics.
The filename must contain an eight-digit date immediately before `.xls`, such
as `portfolio_20250726.xls`.

Run the importer from the project root with:

```bash
poetry run python -m portfolio_advisor.main
```

PyCharm may also execute `src/portfolio_advisor/main.py` directly; the entry
point supports both direct-script and package-module execution.

### Knowledge-graph scripts

The shell scripts in `scripts/` are operational wrappers around Graphify; they
are not agents:

- `scripts/gquery.zsh` — runs a query against `data/knowledge`.
- `scripts/gupdate.zsh` — rebuilds the Graphify graph after knowledge changes.
- `scripts/check_graphify.zsh` — validates Graphify outputs and runs a smoke
  query.
- `scripts/update_graphify.zsh` — upgrades the installed Graphify CLI.

Use these wrappers rather than invoking `graphify query` from the project root,
because Graphify expects its knowledge corpus to be the current directory.

## Deterministic advisor workflow

### Advisor orchestration

`src/portfolio_advisor/advisor/service.py` exposes
`CapitalPreservationAdvisor.evaluate()`. It reads the latest date through
`database/repository.py`, calculates metrics, loads rules, filters, scores,
and ranks. `AdvisorResult` contains the selection, alternatives, rejected
candidates, score contributions, warnings, assumptions, and observation date.
It does not contain financial formulas or mutate SQLite. Tests:
`tests/test_advisor_integration.py`.

### Metrics

`src/portfolio_advisor/metrics/calculations.py` implements documented
return-series formulas (return, volatility, drawdown, downside deviation,
Sharpe, Sortino, historical VaR/CVaR). `portfolio.py` aggregates only the
reported, latest-date database indicators with allocation coverage metadata.
Unavailable data-dependent metrics are explicit. Tests:
`tests/test_calculations.py`, `tests/test_portfolio_metrics.py`.

### Ranking

`src/portfolio_advisor/ranking/` separates rule loading, eligibility,
normalization, scoring, and stable tie-broken ranking. Numeric policy is read
from `data/knowledge/validated_rules/capital_preservation_ranking.yaml`; a
proposed policy requires explicit opt-in. Tests: `tests/test_ranking.py`.

### Knowledge boundary

Graphify is used only for methodology navigation. `EXTRACTED` edges may
provide source-backed context; `INFERRED` edges must not become executable
financial rules unless independently reviewed in a YAML or Markdown rule.

## Historical and backtesting workflow

`src/portfolio_advisor/history/` provides chronological source-date discovery,
point-in-time holdings access, fixed forward-window construction, and read-only
access to an optional `portfolio_nav_history` table. The production SQLite
database has snapshot indicators only; no NAV schema is added or migrated
automatically. Invalid NAV history fails closed, while absent or incomplete
windows are returned as explicit incomplete outcomes.

`src/portfolio_advisor/backtesting/service.py` exposes
`WalkForwardBacktester.run()`. It calls
`CapitalPreservationAdvisor.evaluate(observation_date=...)` at each evaluation
date, so all Milestone 2 eligibility, normalization, scoring, and tie-breaking
remain shared. Forward metrics use only later NAV checkpoints within the
requested 90-, 180-, or 365-day window. Tests:
`tests/test_history.py`, `tests/test_backtesting.py`.

## Updating this document

When an agent is implemented, replace its placeholder section with the actual
module path, public entry points, inputs, outputs, state, error behavior, and
the callers or agents it interacts with. Add tests under `tests/` and link to
those tests from the relevant section.
