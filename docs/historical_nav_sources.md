# Historical source evidence and fail-closed rules

## Purpose and separation

Historical source acquisition is an evidence workflow, not a ranking or
backtesting runtime dependency. Approved observations are validated, retained
locally with provenance, and then consumed offline by the existing strict
resolver. Ranking, label construction, and backtesting never fetch a provider
implicitly.

The source-neutral canonical store is
`database/official_historical_nav.sqlite`. It is local-only because it can
contain provider-controlled observations and retained provenance. Each accepted
observation records ISIN, date, value, currency, value type, provider, source
identifier, provenance reference, and quality status.

## Admission and precedence

The established provider hierarchy and source-specific semantics remain in
force. Existing integrated Erste and OeKB paths may be used only through their
validated adapters; Morningstar, lifecycle, corporate-action, MNB, and KELER
evidence retain their existing admission boundaries. A new provider is not
admitted merely because it returns values.

An observation must have exact instrument identity, expected currency,
supported NAV/price semantics, valid date, a positive finite value,
deterministic ordering, and conflict-free duplicate handling. Identical
reimports are idempotent. Conflicting duplicate observations fail closed.

No acquisition or source-resolution path may interpolate, fill, select a
nearby date, use a proxy, synthesize a price/cash flow, or generically stitch
providers.

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
