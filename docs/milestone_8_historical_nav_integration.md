# Milestone 8 — Historical NAV integration

The ignored parallel schema-v3 store receives only retained,
source-neutral `asset_nav_observations` with a valid explicit ISIN already in
the canonical instrument master. Unknown ISINs remain unresolved and are not
imported. No name matching, provider selection, proxy, stitching,
interpolation, portfolio NAV, return series or cash flow is created.

Run a copy-on-write dry run, then explicitly apply and validate:

```bash
poetry run python scripts/integrate_schema_v3_historical_nav.py
poetry run python scripts/integrate_schema_v3_historical_nav.py --apply
poetry run python scripts/validate_schema_v3_historical_nav.py
```

`instrument_nav_observation` retains the canonical instrument key, date,
positive NAV, currency, provider, identifier, provenance, quality and source
fingerprint. Replay is idempotent; a changed row with the same deterministic
identity fails closed. The legacy NAV store remains authoritative and read-only.
Cutover remains **NOT_AUTHORIZED**.
