# Workbook import layer

This package imports dated model-portfolio Excel workbooks into SQLite.
It owns the `modell portfóliók` model-portfolio worksheet only. The separate
`shortlist` worksheet is governed by the schema-v3 shortlist import pipeline;
current LTIA holdings and cash are separate local evidence. See
[Portfolio workflow and current availability](../../../docs/portfolio_workflow_status.md)
for the three-input workflow and present availability boundary.

## Contract

The importer accepts only a visible `modell portfóliók` worksheet (or its
supported English equivalent), fills merged cells, normalizes supported
Hungarian/English headers and categories, and rejects unknown schema values.
The workbook filename must end with an eight-digit date before `.xls`, for
example `portfolio_20250726.xls`; that date is persisted as `2025/07/26`.

Rows are imported atomically into the `model_portfolios` table. Existing dates
are skipped, incompatible schemas fail closed, and a successful workbook is
moved only after its transaction commits.

## Paths and commands

The portable defaults are repository-relative:

| Purpose | Path |
|---|---|
| Input | `data/xls/import` |
| Processed workbooks | `data/xls/processed` |
| SQLite database | `database/model_portfolio.sqlite` |

Run the normal application importer:

```bash
poetry run python -m portfolio_advisor.main --import
```

Use the dedicated module for custom paths:

```bash
poetry run python -m portfolio_advisor.DB_creation.database_create \
  --input-directory /path/to/import \
  --processed-directory /path/to/processed \
  --database /path/to/model_portfolio.sqlite
```

`excel_processing.py` owns worksheet normalization, `database_create.py` owns
schema validation and SQLite transactions, and `text_normalization.py` owns
Unicode-normalized categorical matching.
