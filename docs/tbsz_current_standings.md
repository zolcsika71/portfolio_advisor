# Current LTIA projection (legacy TBSZ compatibility)

The former one-time `database/tbsz_current_portfolio.sqlite` read model and its
builder are retired. `database/tbsz_portfolio.sqlite` remains the private LTIA
evidence authority and is not rewritten to materialize current state.

The supported read-only projection is provided by
`portfolio_advisor.tbsz.repository.TbszPortfolioRepository` together with
`portfolio_advisor.tbsz.service.current_account_state` and
`current_portfolio_records`. For each LTIA account, positions and ordinary PDF
cash evidence select their own latest dated source, with snapshot ID as the
deterministic tie-break. A validated screenshot CASH correction is selected
only by following its explicit same-account supersession chain from that PDF
base. A subsequently selected dated PDF therefore replaces the old base and is
not overridden by an undated correction. Competing successors, cycles, and
cross-account/view links fail closed. Equivalent source snapshots remain
retained in lineage but materialize once.

Use the existing account-level reader from the repository root:

```bash
poetry run python scripts/show_tbsz_current_portfolio.py --account "TBSZ 2024"
```

Cash remains separate by account and currency without FX conversion. Missing
cash remains absent rather than becoming zero, and an unknown source date
remains `NULL`. The retained `data/tbsz/current_standings_confirmations.json`
is historical input evidence for the retired read model; no active workflow
uses it to recreate a database.
