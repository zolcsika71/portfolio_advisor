# Milestone 11C — Reference-rate schema and contract foundation

This bounded foundation defines how official €STR, SOFR, and HUFONIA evidence can later be
represented. It performs no network acquisition, ingestion, benchmark alignment, compounding,
portfolio calculation, production database migration, or runtime enablement.

Production remains `IMPLEMENTED_BLOCKED_BY_DATA` and `NOT_AUTHORIZED`. The installed derived
database may receive only the validated additive feature at the authorized Phase A release
checkpoint; retained source databases and financial observations remain unchanged.

## Immutable domain contracts

`portfolio_advisor.reference_rates` provides frozen contracts for:

- a versioned benchmark definition with currency, administrator, provider series identity, exact
  units, official day-count convention, and official compounding convention;
- a source contract with official and machine-readable URLs, response format, authentication,
  licensing, automated-use, and raw-retention decisions stated explicitly;
- an import manifest that binds an exact HTTPS request, retrieval timestamp, retained raw-artifact
  hash, provider dataset version, validation status, and canonical dataset fingerprint;
- an exact `Decimal` observation with observation/publication dates, provider revision identity,
  supersession lineage, admitted quality, and canonical fingerprint.

There are no implicit endpoints, permission assumptions, zero-rate fills, policy-rate substitutes,
float conversions, missing-date fills, or convention defaults. The policy-binding validator requires
the benchmark name, administrator, currency, and official page to match the reviewed
`CAPITAL_DEFENSIVE_CONSTRUCTION_POLICY` v1.0.0. It does not approve a machine endpoint or licence.

## Additive schema feature

Future from-scratch schema-v3 databases include feature marker
`MILESTONE_11C_REFERENCE_RATE_EVIDENCE` revision 1 and four empty tables:

- `reference_rate_definition` preserves governed benchmark identity and methodology;
- `reference_rate_source` preserves official source and access/licensing decisions;
- `reference_rate_import_manifest` preserves request and immutable raw-artifact provenance;
- `reference_rate_observation` preserves exact decimal values, dates, quality, and revisions.

Composite foreign keys prevent a manifest or observation from crossing benchmark/source identity.
Only one current observation can exist for a benchmark/date, corrections must use explicit revision
lineage, and definitions, sources, manifests, or observations cannot be removed while referenced.

The normal schema validator remains backward compatible with the installed Milestone 11B database:
complete absence of this new feature is accepted, while any partial installation, stale marker,
foreign-key violation, or integrity failure is rejected.

## Disposable migration boundary

`scripts/migrate_reference_rate_schema.py` can build an explicitly named candidate outside the
repository. It never installs that candidate. The migration revision is
`MILESTONE_11C_REFERENCE_RATE_SCHEMA_V1`.

The candidate builder:

1. opens the source read-only and checks schema v3, integrity, and foreign keys;
2. copies it using SQLite backup;
3. installs only the empty reference-rate feature and marker transactionally;
4. fingerprints every pre-existing value, including Milestone 11B feature rows;
5. requires migrated and from-scratch reference-rate schema contracts to match;
6. verifies the source database hash did not change.

Installation remains an operator-controlled checkpoint: preserve the installed database outside
the repository, atomically replace it only with the validated candidate, and restore the backup if
any post-installation gate fails. No application code performs migration or cutover implicitly.

`scripts/validate_reference_rate_schema.py` is the read-only installed-state gate. Its canonical
JSON verifies the exact from-scratch schema contract, feature marker, integrity, foreign keys, and
zero rows in all four production reference-rate tables. Repeated runs are byte-identical.

## Remaining gates

Before any observation can be admitted, later work must establish and review the exact official
endpoint, response parser, licence and raw-retention permission, revision behavior, publication
calendar, missing-value semantics, and official day-count/compounding metadata for each currency.
After ingestion, separate work is still required for benchmark-to-portfolio-date alignment, Sharpe,
Sortino, portfolio metrics, ranking, and production cutover.
