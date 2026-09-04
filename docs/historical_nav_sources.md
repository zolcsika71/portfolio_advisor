# Historical source evidence and fail-closed rules

## Purpose and separation

Historical source acquisition is an evidence workflow, not a ranking or
backtesting runtime dependency. Approved observations are validated, retained
locally with provenance, and then consumed offline by the existing strict
resolver. Ranking, label construction, and backtesting never fetch a provider
implicitly.

The source-neutral legacy store is `database/official_historical_nav.sqlite`.
Its selected Milestone 8 projection also remains in `portfolio_advisor.sqlite`.
Phase E adds separate immutable provenance tables to the latter database for
new exact-share-class EUR/HUF evidence. Legacy rows are neither reinterpreted
nor silently promoted into the Phase E contract.

## Admission and precedence

The established provider hierarchy and source-specific semantics remain in
force. Existing integrated Erste and OeKB paths may be used only through their
validated adapters; Morningstar, lifecycle, corporate-action, MNB, and KELER
evidence retain their existing admission boundaries. A new provider is not
admitted merely because it returns values.

An observation must have exact instrument and share-class identity, expected currency,
supported NAV/price semantics, valid date, a positive finite value,
deterministic ordering, and conflict-free duplicate handling. Identical
reimports are idempotent. Conflicting duplicate observations fail closed.

No acquisition or source-resolution path may interpolate, fill, select a
nearby date, use a proxy, synthesize a price/cash flow, or generically stitch
providers.

Phase E identifies Erste Market as
`APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE`, never as an authoritative NAV
administrator. Its ordinary chart contract remains `application/json`. The
exact Erste Market HTTPS numeric-chart endpoint alone may undergo a separate
offline semantic assessment when it returns whole-body strict JSON labelled
exactly `text/html; charset=utf-8`; the original response remains transport-
quarantined and a distinct immutable semantic receipt records admission. This
exception does not weaken any other provider, host, endpoint, or media type.

See [Milestone 11C Phase E](milestone_11c_phase_e_nav_provenance.md) for the
admitted cohorts, hashes, schema lineage, and offline commands.

## Strict unresolved cases

- `HU0000554795` remains
  `BACKTEST_UNRESOLVABLE_WITH_CURRENT_PUBLIC_EVIDENCE`. MNB/KELER weekly OTC
  aggregates are not NAV and must not be converted into NAV, maturity value,
  coupon, redemption, or a synthetic return.
- `AT0000605324` remains `RECONCILIATION_REQUIRED`. No conflicting Erste,
  OeKB, or other value is automatically selected to create coverage.

Corporate-action and lifecycle lineage are evidence references, not generic
permission to stitch histories or create cash flows.

## Local artifacts and commands

Audit artifacts and raw evidence live under `data/audit/` and `data/raw/` and
are ignored by Git. They preserve deterministic reports and provider evidence
locally without committing large or controlled data. Rebuild relevant local
diagnostics with:

```bash
poetry run python scripts/plan_historical_nav_acquisition.py
poetry run python scripts/audit_backtest_window_coverage.py
poetry run python scripts/validate_strict_backtest_pipeline.py
poetry run python scripts/build_official_forward_label_store.py
```

Explicit acquisition commands are the only workflows permitted to contact a
provider. Tests use fixtures and never require live network access.
