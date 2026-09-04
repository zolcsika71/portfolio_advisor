# One-time LTIA current-standings database (legacy TBSZ compatibility)

`scripts/create_tbsz_current_portfolio_once.py` creates the isolated local
read-model database `database/tbsz_current_portfolio.sqlite` from the retained,
manually confirmed George PDFs in legacy compatibility path `data/tbsz/source/`.

Run it once from the repository root:

```bash
poetry run python scripts/create_tbsz_current_portfolio_once.py
```

The database records the observed current LTIA investments and cash balances,
with source-document filename and SHA-256 provenance. It keeps position and cash
rows normalized, and exposes their read-only union through `current_holdings`.
It preserves the source EUR, USD, and HUF rows without FX conversion.

It does not contain BUY or SELL recommendations, target allocations, executed
trades, or a transaction ledger. Unsupported ISIN, quantity, unit price, ROI,
and source-date fields remain `NULL`.

Creation refuses to overwrite an existing output. `--force` is deliberate: it
first writes and verifies an ignored SQLite backup under `database/backups/`,
then replaces the read-model database. Source PDFs, confirmation data, the
output database, and backups are all local-only and ignored by Git.
