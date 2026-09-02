# Milestone 11C Phase A — Reference-rate schema and contract foundation

This bounded Phase A foundation defined how official €STR, SOFR, and HUFONIA evidence could later
be represented. At completion it performed no network acquisition, ingestion, benchmark alignment,
compounding, portfolio calculation, or runtime enablement.

Phase B subsequently admitted one official ECB €STR history artifact without changing this schema
contract. See [Milestone 11C Phase B](milestone_11c_phase_b_ecb_estr_ingestion.md). Production
remains `IMPLEMENTED_BLOCKED_BY_DATA` and `NOT_AUTHORIZED`.

> **Forward correction:** this document records the historical revision-1
> contract. Phase C0 proved that mandatory non-empty `publication_date`,
> `provider_revision_id`, and `provider_dataset_version` fields were too
> provider-specific. The installed schema is now provider-neutral revision 2;
> see [Milestone 11C Phase C0](milestone_11c_phase_c0_reference_rate_provenance_contract.md).
> Revision-1 fingerprints below remain historical identities and are not
> reinterpreted as revision 2.

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

Phase A from-scratch schema-v3 databases include feature marker
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

At Phase A completion, `scripts/validate_reference_rate_schema.py` was the read-only
empty-foundation gate. Its canonical
JSON verifies the exact from-scratch schema contract, feature marker, integrity, foreign keys, and
zero rows in all four production reference-rate tables. Repeated runs are byte-identical. It
intentionally rejected the populated Phase B target; Phase B had a separate read-only validator.
Phase C0 updates this command to validate the exact current v2 structure whether empty or populated,
while `scripts/validate_reference_rate_provenance.py` validates all stored bundles and evidence.

## Remaining gates

Phase B established those source and parser facts for ECB €STR and admitted EUR evidence. SOFR and
HUFONIA still require their own reviewed adapters. Separate work is still required for
benchmark-to-portfolio-date alignment, Sharpe, Sortino, portfolio metrics, ranking, and production
cutover.
