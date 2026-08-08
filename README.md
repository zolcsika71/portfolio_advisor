# Portfolio Advisor

Portfolio Advisor imports model-portfolio Excel data into SQLite and provides a
deterministic capital-preservation analytics and ranking workflow. No LLM is
used for calculations, scoring, ranking, or selection.

## Requirements

- Python 3.12
- Poetry
- Excel `.xls` files containing a visible `modell portfóliók` worksheet

Install dependencies with:

```bash
poetry install
```

## Import workflow

The importer:

1. Finds `.xls` files in the input directory.
2. Reads only the visible `modell portfóliók` worksheet.
3. Fills merged cells and removes empty rows and columns.
4. Translates supported headers and categorical values into English.
5. Creates one `Date` field per row from the eight-digit date in the filename.
6. Inserts the normalized data into SQLite.
7. Moves successfully processed files to the processed directory.

## Default paths

| Purpose | Path |
|---|---|
| Input Excel files | `data/xls/import` |
| Processed files | `data/xls/processed` |
| SQLite database | `database/model_portfolio.sqlite` |

The processed directory and database parent directory are created
automatically.

## Running deterministic analysis

From the project root:

```bash
poetry run python -m portfolio_advisor.main
```

The command emits a structured JSON result, discovers the latest observation
date automatically, and opens SQLite in read-only mode. The shipped policy is
deliberately marked `proposed`; the default command reports this and makes no
selection until it has independent review. To evaluate that proposal in a
controlled setting, opt in explicitly:

```bash
poetry run python -m portfolio_advisor.main \
  --allow-proposed-rules \
  --top-alternatives 3
```

Use `--rules` and `--database` to supply reviewed rules and an alternative
source database. Rules, weights, directions, coverage requirements, and
allocation tolerances live in
`data/knowledge/validated_rules/capital_preservation_ranking.yaml`.

Supported current-schema indicators are allocation-weighted one-year return,
reported annualized volatility, maximum drawdown, downside risk, Sharpe ratio,
unhedged allocation, and currency concentration. VaR/CVaR, Sortino, reconstructed
drawdown, liquidity, cost, and true currency mismatch need data not present in
the source schema and are reported as unavailable.

Every analysis result records its source observation date, policy status,
rule-set version, and whether `--allow-proposed-rules` was explicitly supplied.
If the selected rules file is missing, malformed, lacks a version, or is a
proposed policy without that opt-in, the advisor fails closed: it returns no
selection and no ranking, with `rules_status: "unavailable"` and the reason in
`warnings`. It never substitutes a default policy.

See [the methodology reference](docs/methodology.md) for the formulas,
annualization assumptions, SQLite field mapping, reported-versus-recomputed
metric boundary, ranking method, and policy controls.

## Historical backtesting

Milestone 3 adds a deterministic, read-only walk-forward layer that reuses the
existing ranking engine at each historical observation date. The production
database contains reported snapshot indicators, but no NAV/price or periodic
return series; it therefore produces explicit incomplete forward outcomes
rather than fabricated backtest returns. See
[the backtesting methodology](docs/backtesting.md) for point-in-time rules,
the proposed optional NAV-history schema, horizons, metrics, and baselines.

Historical NAV acquisition records source precedence and provenance separately
from ranking and backtesting. Erste Market is the configured primary source;
there is currently no configured secondary provider. The seven unmapped ISINs
therefore remain fail-closed as `SECONDARY_SOURCE_REQUIRED`, while conflicting
history requires an independent-source reconciliation. See [the historical NAV
source contract](docs/historical_nav_sources.md).

## Running the importer

The existing Excel import workflow remains available:

```bash
poetry run python -m portfolio_advisor.main --import
```

For custom importer paths, run the dedicated importer module:

```bash
poetry run python -m portfolio_advisor.DB_creation.database_create \
  --input-directory /path/to/import \
  --processed-directory /path/to/processed \
  --database /path/to/model_portfolio.sqlite
```

## Input filename requirements

Each workbook must have the `.xls` extension and contain an eight-digit date
immediately before the extension. For example:

```text
portfolio_20250726.xls
```

The date is stored as `2025/07/26` in the database. Workbooks without a valid
date or supported target worksheet remain in the input directory when
processing fails.

## Database schema

The importer creates the `model_portfolios` table with:

- `Date`
- `Portfolio Name`
- `Product`
- `ISIN`
- `Allocation (%)`
- `Asset Class`
- `Sub-Asset Class`
- `Currency`
- `Currency Risk`
- `Sustainability`
- `YTD`, `1 Year`, `3 Years`, `5 Years`
- `1Y Sharpe Ratio`, `3Y Sharpe Ratio`, `5Y Sharpe Ratio`
- `1Y Volatility`, `3Y Volatility`
- `Downside Risk`, `Information Ratio`, `Maximum Drawdown`

See [`src/portfolio_advisor/DB_creation/README.md`](src/portfolio_advisor/DB_creation/README.md)
for implementation details.

## Development checks

Run Ruff and compile the package with:

```bash
poetry run ruff check src
poetry run pytest
poetry run mypy src
```
