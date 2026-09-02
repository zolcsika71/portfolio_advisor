# Milestone 11C Phase C0: provider-neutral reference-rate provenance

Status: **COMPLETE / GO** for the provenance-contract remediation only. This
checkpoint admitted no SOFR, HUFONIA, NAV, FX, portfolio, or metric evidence.

## Why revision 1 was not representable

The Phase A revision-1 contract required every provider to supply a non-empty
`publication_date`, `provider_revision_id`, and `provider_dataset_version`.
That assumption was too provider-specific. An official response may truthfully
contain value dates and exact rates while omitting an observation revision ID,
a dataset version, or an exact publication timestamp. Retrieval timestamps,
artifact hashes, value dates, request IDs, generated UUIDs, internal sequence
numbers, and labels such as `INITIAL` must never be relabelled as provider-issued
metadata.

Phase C0 replaces that assumption with provenance contract version 2. Provider
fields are optional only when absent from the official evidence, while internal
evidence identity and conservative availability evidence remain mandatory.

## Identity model

Provider-issued and system-generated identity are separate axes:

- `provider_dataset_version` and its source field are a paired nullable value.
- `provider_revision_id` and its source field are a paired nullable value.
- `provider_revision_indicator` preserves the exact raw value. SQL `NULL`
  means that the field was not supplied; `""` means it was supplied empty.
- `internal_evidence_identity` is mandatory, system-labelled, and derived under
  `SYSTEM_CANONICAL_ARTIFACT_V1` from source identity, request/response
  provenance, retrieval time, artifact reference, and artifact SHA-256.
- The internal identity remains distinct from provider metadata, the rate,
  dataset fingerprint, raw artifact hash, and canonical manifest fingerprint.
- Every manifest stores its canonical `manifest_fingerprint` and is unique by
  source plus internal identity scheme and value.

The validator reconstructs each identity and fingerprint. It rejects a provider
field/source-field mismatch, an absent internal identity, a noncanonical
manifest, artifact tampering, or a system value presented as provider metadata.

## Availability and no-lookahead contract

Every admitted observation has one fixed-width UTC
`availability_boundary_utc`. Historical as-of selection first filters on that
boundary and then selects the latest authorized revision available at the
cutoff. It never uses the present-day `is_current` projection to decide what
was historically knowable.

The three supported bases are:

- `PROVIDER_REPORTED`: requires the exact provider timestamp, its kind, and its
  source field. The UTC boundary must equal that timestamp.
- `OFFICIAL_SCHEDULE_DERIVED`: requires a versioned derivation rule, an
  authoritative HTTPS policy reference, an approved calendar identity and
  version, the calendar fingerprint, exact benchmark/source binding, and
  reproducible lookup for the value date. Genuine provider `DATE` metadata may
  be retained separately; it is not relabelled as the derived timestamp. A
  weekend-only guess or unversioned prose is not sufficient.
- `RETRIEVAL_BOUND`: requires the boundary to equal the exact manifest
  retrieval instant. The observation is unavailable before capture, even when
  its value date is historical.

All boundaries must be on or after the observation value date and no later
than artifact retrieval. Revision boundaries must be nondecreasing. Date-only
or naive cutoffs fail closed. One as-of benchmark series cannot stitch sources
across dates.

## Revision semantics

Provider state distinguishes:

- `PROVIDER_EXPLICIT_REVISION`;
- `PROVIDER_EXPLICIT_NO_REVISION`;
- `PROVIDER_EMPTY_REVISION_INDICATOR`;
- `PROVIDER_REVISION_FIELD_NOT_SUPPLIED`.

Evidence comparison separately distinguishes `IDENTICAL_REPLAY`,
`INTERNAL_EVIDENCE_SNAPSHOT_CHANGED`, `AUTHORIZED_PROVIDER_REVISION`, and
`CONFLICTING_EVIDENCE`. A changed value is conflicting unless an explicit
provider-revision signal and a separately validated, versioned provider
revision-transition contract both authorize an append. The persisted contract
identity binds its ID/version, authoritative reference, benchmark, source,
raw indicator source field, and exact raw indicator value; sequence 1 has no
transition contract, while every later sequence requires all five stored
contract fields and an exact approved-registry fingerprint. Evidence is never
overwritten in place.

## Contract and fingerprint transition

The feature-level migration identity is
`MILESTONE_11C_PHASE_C0_REFERENCE_RATE_PROVENANCE_V2`. The global SQLite schema
remains schema v3; the reference-rate feature and provenance contract advance
from revision 1 to revision 2.

| Identity | Revision 1 | Revision 2 |
|---|---|---|
| Feature fingerprint | `aace6e9cf4b33fbf9cad503a987b945ebb44e1db5673381fd47cbd57716d921b` | `3add5fa914e71807fff2add5b369a3ef80f50c2d22844743cfb74b00680cfe71` |
| Schema-contract fingerprint | `1d9cb07e1bee4bed81ebe6a58a293ea544249498f736899069452ae167b59d61` | `4862a51f6724b22e7e7aab0c1e914449750077dbd0fa54ca66e2aa478e579404` |
| ECB definition fingerprint | `7eae4e19d31ef73f4fc16683c369c4f0b82708390776ddcccd4eb23c033d5926` | `20f194bc719fa1a0f0971187d6620716e44b8d15c0add2fe44d649bdc6638df3` |
| ECB source fingerprint | `f4103515ef8b7fe3b789ae49922be0a85b4317efe572914672186eddf76d479d` | unchanged |
| ECB manifest fingerprint | `dcf37a54eff7001f83cdddb0fbc4689383d744de8e634e008d396c59d92a3772` | `5cf0a1625d7f50cdf3241e26a4e8807c04c33974f4f99950ac7b8599e1bc775b` |
| ECB observation-set fingerprint | `94ceb817fc24bf9c2e066019c892d31c4cab3e624367f948bb2ed061c896eb8c` | `f2f0f6858764b409d11f87013e9338b28c9071f6c60fc3a8155a03655cf012f4` |

The definition serialization changes only
`contract_schema_version: 1 -> 2`. The source serialization is unchanged. The
manifest retains every v1 key and adds `provenance_contract_version`,
`provider_dataset_version_source_field`, `internal_evidence_identity_scheme`,
and `internal_evidence_identity`. The observation fingerprint payload removes
the v1 `publication_date` key, adds `provider_publication_date`, and adds:
`provenance_contract_version`; provider revision-ID source, raw indicator,
indicator source, and normalized status; five nullable provider-revision
transition-contract keys; provider publication value/kind/source; availability
basis/boundary; and the six nullable derivation-rule/calendar keys. All other
v1 observation-identity keys remain. The mutable `is_current` projection is
present in the full diagnostic payload but is deleted from the fingerprint
payload in both semantics. These intentional changes create new fingerprints;
no revision-1 fingerprint is reinterpreted as revision 2.

## Migration and validation

The migrator classifies the reference-rate feature as exact `ABSENT`, `V1`, or
`V2`. Any partial, mixed, stale, future, constraint-damaged, index-damaged, or
foreign-key-damaged state fails closed. New databases receive v2 directly.
Only the explicit candidate migrator accepts exact v1.

The installed Phase B database could not be migrated truthfully from database
columns alone because revision 1 did not retain ECB `OBS_STATUS`. The migration
therefore verifies the immutable ECB CSV and receipt offline and maps:

- exact `VALID_FROM` to provider revision identity and provider publication
  evidence, both with source field `VALID_FROM`;
- exact `OBS_STATUS` to the raw indicator with source field `OBS_STATUS`;
- `A` to `PROVIDER_EXPLICIT_NO_REVISION` and `R` to
  `PROVIDER_EXPLICIT_REVISION` under the reviewed ECB adapter;
- normalized `VALID_FROM` to `PROVIDER_REPORTED` availability.

Run from the repository root, supplying the retained artifact pair:

```bash
poetry run python scripts/migrate_reference_rate_provenance_contract.py \
  --source database/portfolio_advisor.sqlite \
  --candidate /absolute/disposable/portfolio_advisor.candidate.sqlite \
  --raw-artifact data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.csv \
  --receipt data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.receipt.json
poetry run python scripts/validate_reference_rate_schema.py \
  --target /absolute/disposable/portfolio_advisor.candidate.sqlite
poetry run python scripts/validate_ecb_estr_reference_rate.py \
  --target /absolute/disposable/portfolio_advisor.candidate.sqlite \
  --raw-artifact data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.csv \
  --receipt data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.receipt.json
poetry run python scripts/validate_reference_rate_provenance.py \
  --target /absolute/disposable/portfolio_advisor.candidate.sqlite
# When schedule/revision governance is needed, add the reviewed offline file:
poetry run python scripts/validate_reference_rate_provenance.py \
  --target /absolute/disposable/portfolio_advisor.candidate.sqlite \
  --validation-registry /absolute/reviewed/reference-rate-registry.json
poetry run python scripts/migrate_reference_rate_provenance_contract.py \
  --target /absolute/disposable/portfolio_advisor.candidate.sqlite \
  --raw-artifact data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.csv \
  --receipt data/raw/reference_rates/ecb/estr/estr-e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8.receipt.json
```

All Phase C0 migration and validation operations are offline. The migration reconstructs the four
reference-rate tables transactionally, preserves explicit row IDs and
relationships, updates the feature marker last, and restores foreign-key
enforcement. Exact v2 replay executes no writes and leaves database bytes
unchanged. Validators open SQLite read-only with `query_only=ON` and emit
deterministic sorted JSON.

## Preserved official ECB evidence

Phase C0 changed only the authorized schema/provenance representation. It
preserved the official €STR dataset exactly:

- definitions/sources/manifests/observations: `1 / 1 / 1 / 1,771`;
- value-date range: `2019-10-01` through `2026-08-31`;
- dataset fingerprint:
  `99a1a2ff837688bb78fd0b81cbef1ef64f27f1cab36cc2acdb0ded5026cc534e`;
- raw artifact SHA-256:
  `e9c8c20cde58d7805fec11851f180fdd44e5354b61562a294b6a49492b7474d8`;
- receipt file SHA-256:
  `461e6f518c079b70ebbeaff917d2621904b2efe40aafc2b035b28d8237333abf`;
- receipt fingerprint:
  `a180e89e9806d1e307d5dd6590d6c9844d27f4593961a2f1f6d405aeb5561f9c`;
- system snapshot identity:
  `b2b0f7b91b5cfccfa0767a6695c9b0d01bb7c1a9f1c6db1a0a49b3b4bdb2a54c`.

Dates, exact Decimal rates, provider values, request provenance, artifact
reference, retrieval timestamp, IDs, revision sequences, supersession links,
and current projections reconcile row by row. All 1,771 exact raw
`OBS_STATUS` values are `A`.

Raw responses and receipts remain immutable local evidence under
`data/raw/reference_rates/`. They are ignored by Git and must be included in
the operator's durable, access-controlled backup regime together with the
installed database. Offline validation cannot reproduce provenance if either
member of the retained pair is lost.

## Installation evidence

The verified external rollback backup retained the pre-migration database
SHA-256
`19e6efdadfd44235408cb046d6af38b593cf88a76dfc7ef1f815aef8769ae5d2`.
The disposable candidate and atomically installed database both have SHA-256
`5e9eb3b64b5fdbac3599a25b248a65e702c0404fc8239cd3458a6246ef0767cd`.
An exact v2 replay returned `reused: true`; its before and after database
hashes were identical. The revision-1, candidate, and installed logical
preservation projections all have SHA-256
`93f338d887c4dab622898bd6cad78b64b3f74abdcddbfd14c1d06575f68f985e`.

Repeated read-only audits were byte-identical. Their canonical JSON SHA-256
values are:

- schema audit:
  `aecd038f048d7ac51d394a9449920dbeaa6bfc2a7e2c4f1a2431c27e04ce8572`;
- complete reference-rate provenance audit:
  `79f00f66844ac11d74095a6f47de64fb441543849df09dc5d6dbedb6d655bfa1`;
- ECB evidence audit:
  `5863ab4d93b21e61b75d53d09c2aea94b91f1b89d0f9105827e3dd6bcd6e1bd4`.

Both candidate and installed databases passed `integrity_check = ok` with zero
foreign-key violations. No SOFR rows or constructed-portfolio production rows
were created.

## Roadmap boundary

SOFR remains unadmitted: the diagnostic response that exposed revision 1's
representability problem was not copied, imported, or used to populate the
database. Phase C SOFR acquisition/import must be retried as a separate
authorized checkpoint against the v2 contract.

HUFONIA remains `NOT_STARTED`. NAV remediation remains pending. Portfolio
metrics, ranking activation, and real construction remain blocked. Overall
Milestone 11 remains **NO-GO**; Milestones 12 and 13 remain **NO-GO**; production
cutover remains **NOT_AUTHORIZED**.

Immediate next checkpoint: retry official SOFR Phase C under the provider-neutral
v2 provenance contract. That authorization does not extend to HUFONIA, FX, NAV,
portfolio metrics, construction, rebalancing, trading, or production cutover.
