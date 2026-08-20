# Strict backtesting and prospective validation

## Historical backtesting boundary

The walk-forward backtester evaluates the same point-in-time ranking policy at
each decision date. It constructs separate 90-, 180-, and 365-calendar-day
windows and uses only later data for outcome metrics. A forward outcome is
official only when the exact portfolio and full required history pass the
strict source and eligibility gates.

`DIAGNOSTICS_ONLY`, rejected, reconciled, unusable, or incomplete source
results are never performance labels. Missing data remains unavailable: the
implementation does not drop constituents, renormalize weights, use cash/zero
returns, proxies, interpolation, fills, source stitching, or nearby-date
substitution.

The official label store keeps an explicit record for every candidate
date/portfolio/horizon. It separates available official metrics from
unavailable labels and retains blocking ISINs, categories, source provenance,
and interval metadata. It currently contains no admitted historical portfolio
labels because no direct official portfolio series is retained and synthetic
portfolio reconstruction is not approved.

## Constituent evidence is not portfolio performance

The local `official_historical_nav.sqlite` store contains validated
constituent-level observations with source provenance. Those observations may
improve constituent coverage diagnostics but cannot be summed into a portfolio
NAV without established portfolio-specific allocation, rebalance, currency,
cash-flow, and total-return semantics.

The reconstruction assessment is therefore frozen as
`PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED`. This freeze blocks only a
synthetic constituent-to-portfolio path; a later direct official portfolio NAV
or performance export remains a separate, admissible route when identity,
semantics, intervals, and provenance are validated.

## Prospective validation

Prospective validation preserves what the advisor knew before future outcomes
exist:

```text
decision-time inputs -> finalized append-only decision -> pending horizon slots
-> due monitor -> explicit direct-source admission or unavailable status
```

`database/prospective_portfolio_validation.sqlite` contains finalized records,
their complete candidate universes, and 90/180/365-day slots. Historical schema
replays are marked `RESEARCH_BACKFILL` and are excluded from prospective
readiness. Genuine records are `PROSPECTIVE_LIVE_RECORD` and cannot be
rewritten after finalization.

The offline monitor classifies only genuine live slots as `NOT_YET_DUE` or due.
It does not fetch providers, calculate returns, admit outcomes, or close a
slot merely because time elapsed. Outcome admission requires a due slot and a
provenance-backed direct portfolio-level source. Permitted source types are:

- `DIRECT_OFFICIAL_PORTFOLIO_NAV`
- `DIRECT_OFFICIAL_PORTFOLIO_PERFORMANCE_EXPORT`
- `APPROVED_PROSPECTIVE_PORTFOLIO_OBSERVATION`

Constituent aggregation, synthetic NAV, Graphify inference, and unsupported
manual returns are blocked.

## Operations

```bash
poetry run python scripts/record_prospective_portfolio_decision.py --record-type live
poetry run python scripts/check_due_prospective_outcomes.py
poetry run python scripts/audit_prospective_portfolio_validation.py
poetry run python scripts/schedule_prospective_outcome_due_checks.py
```

The scheduler derives one rolling future due check from the ledger and invokes
only the offline monitor and audit. It never acquires a source or admits an
outcome. On macOS, `--install` is an explicit current-user LaunchAgent action.

Until genuine approved portfolio-level outcomes mature, the states remain
`PROSPECTIVE_VALIDATION_NOT_READY` and `OPTIMIZATION_NOT_READY`.
