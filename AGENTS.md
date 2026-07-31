# Agents

## Current implementation status

This repository does not currently contain implemented software agents. A search
of the Python source found no agent classes, agent functions, orchestration
code, LLM integration, or agent-specific prompt files.

The following packages exist but are currently empty apart from `__init__.py`
files, so they are namespaces rather than agents:

- `src/portfolio_advisor/advisor/`
- `src/portfolio_advisor/knowledge/`
- `src/portfolio_advisor/metrics/`
- `src/portfolio_advisor/ranking/`

`src/portfolio_advisor/main.py` is the application entry point and delegates to
the database-import workflow; it does not start an agent workflow.

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

## Planned agent placeholders

These are placeholders only. No behavior should be attributed to them until
corresponding implementation files and tests are added.

### Portfolio advisor agent — not implemented

- Intended role: `[describe the user-facing advisory responsibility]`
- Intended inputs: `[portfolio data, constraints, risk profile, or query]`
- Intended outputs: `[recommendation, explanation, report, or other result]`
- Planned implementation file: `src/portfolio_advisor/advisor/[file].py`
- Interactions: `[list the ranking, metrics, knowledge, and database components
  it will call]`

### Knowledge agent — not implemented

- Intended role: `[describe how financial knowledge is retrieved or cited]`
- Intended inputs: `[natural-language query or structured topic]`
- Intended outputs: `[retrieved evidence or structured context]`
- Planned implementation file: `src/portfolio_advisor/knowledge/[file].py`
- Interaction constraint: Graphify `EXTRACTED` edges may provide
  source-backed methodology context; `INFERRED` edges are for discovery and
  navigation only and must not become executable financial rules unless the
  rule also appears in a reviewed YAML or Markdown specification.

### Metrics agent — not implemented

- Intended role: `[describe the portfolio metrics it will calculate]`
- Intended inputs: `[holdings, returns, benchmark, and time period]`
- Intended outputs: `[metrics and calculation metadata]`
- Planned implementation file: `src/portfolio_advisor/metrics/[file].py`
- Interactions: `[describe how it will consume imported data and expose results]`

### Ranking agent — not implemented

- Intended role: `[describe how portfolios or products will be ranked]`
- Intended inputs: `[candidate portfolios, metrics, constraints, and preferences]`
- Intended outputs: `[ordered candidates and rationale]`
- Planned implementation file: `src/portfolio_advisor/ranking/[file].py`
- Interactions: `[describe dependencies on the metrics, knowledge, and advisor
  components]`

## Updating this document

When an agent is implemented, replace its placeholder section with the actual
module path, public entry points, inputs, outputs, state, error behavior, and
the callers or agents it interacts with. Add tests under `tests/` and link to
those tests from the relevant section.
