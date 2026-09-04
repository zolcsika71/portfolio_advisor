# Milestone 11C Phase E — EUR/HUF NAV provenance evidence

Phase E installs an additive, exact-share-class NAV provenance layer for the
smallest reviewed eight-instrument EUR and HUF cohorts needed by a later
`CAPITAL_DEFENSIVE` construction checkpoint. It prepares evidence only. It
does not align returns, calculate portfolio metrics, construct or persist a
portfolio, select a finalist, or authorize production cutover.

## Source governance and media boundary

The retained source is Erste Market, identified as
`APPROVED_DISTRIBUTOR_NON_AUTHORITATIVE`. It is an approved distributor path
for this evidence; it is **not** an authoritative NAV administrator. Exact
identity pages use
`https://www.erstemarket.hu/befektetesi_alapok/alap/{ISIN}` and chart responses
use `https://www.erstemarket.hu/funds/chart/{numeric-id}`.

The ordinary chart transport contract remains strict `application/json`.
Erste Market returned whole-body JSON from the exact HTTPS chart endpoint with
the exact normalized media type `text/html; charset=utf-8`. Such a response
retains its original `QUARANTINED_REJECTED_RESPONSE` transport classification.
A separate immutable semantic-admission receipt may admit it only when the
host and numeric path are exact, status is 200, no redirect or host change
occurred, the complete bounded body strictly decodes as UTF-8 and parses as
whole-body JSON, and schema, chart ID, retained exact-ISIN identity, currency,
dates, Decimal values, hashes, history, freshness, and duplicate/conflict
checks all pass. HTML, embedded JSON, another host or endpoint, and the same
media type from any other provider remain rejected. Quarantined bytes and
their transport receipts are never rewritten or automatically promoted.

## Admitted cohorts

The evidence cutoff is `2026-08-31`. Every instrument spans at least 365
calendar days and its latest admitted observation is no more than 30 days
before the cutoff. Values are stored as exact Decimal text; there is no float
conversion, interpolation, nearest-date substitution, share-class proxy, or
FX conversion.

| Currency | ISIN | Chart ID | Observations | Admitted range |
| --- | --- | ---: | ---: | --- |
| EUR | `AT0000673322` | 11752 | 249 | 2025-08-29–2026-08-31 |
| EUR | `AT0000A00GL9` | 692 | 249 | 2025-08-29–2026-08-31 |
| EUR | `AT0000A0H8D4` | 7271 | 249 | 2025-08-29–2026-08-31 |
| EUR | `HU0000722442` | 11002 | 249 | 2025-08-29–2026-08-31 |
| EUR | `LU0244270723` | 5812 | 249 | 2025-08-29–2026-08-31 |
| EUR | `LU0594300682` | 8171 | 260 | 2025-08-29–2026-08-31 |
| EUR | `LU1931957093` | 10952 | 205 | 2025-08-28–2026-08-28 |
| EUR | `LU2334866550` | 12970 | 249 | 2025-08-29–2026-08-31 |
| HUF | `AT0000A00GE4` | 332 | 249 | 2025-08-29–2026-08-31 |
| HUF | `HU0000702477` | 392 | 249 | 2025-08-29–2026-08-31 |
| HUF | `HU0000708243` | 3962 | 249 | 2025-08-29–2026-08-31 |
| HUF | `HU0000722434` | 11831 | 249 | 2025-08-29–2026-08-31 |
| HUF | `HU0000723572` | 11011 | 249 | 2025-08-29–2026-08-31 |
| HUF | `LU0979392684` | 6971 | 260 | 2025-08-29–2026-08-31 |
| HUF | `LU0979393062` | 6981 | 260 | 2025-08-29–2026-08-31 |
| HUF | `LU1295422502` | 8361 | 260 | 2025-08-29–2026-08-31 |

EUR totals 1,959 observations across eight exact ISINs, with cohort range
2025-08-28–2026-08-31. HUF totals 2,025 observations across eight exact ISINs,
with cohort range 2025-08-29–2026-08-31. Each cohort contains eight
conflict-free asset/subasset groups. The installed Phase E total is 3,984
observations and 16 import manifests.

## Provenance and fingerprints

The additive schema objects are `nav_evidence_source`, `nav_import_manifest`,
and `nav_observation_version`, two supporting indexes, and six update/delete
immutability triggers. Their feature marker is
`MILESTONE_11C_PHASE_E_NAV_PROVENANCE`, contract revision 1, with fingerprint
`3ca7121ded39f483bda86582764bc2ac0724c18353130ade317092d7ab39d072`.
The installed source fingerprint is
`32ce7e74040f3d1a2b01c7fd386a52096be17e83e940a46a64f869a6694a3511`.

The external retained bundle fingerprints are:

```text
EUR bundle:                373970d6eb8ff9a0f490b8173ce81ef5e8d0c8aaafe8f84e4e8b2c6aa65e9d1e
HUF bundle:                935430c0b40afb66ba5aa53eed82d147113a96fbd91cd06aaa97fc0086e0285c
combined bundle:           21ae02919360bb3e02c6676df3bc183494a9b73fa1fdd16a3feece1599b3fe1b
combined manifest SHA-256: 2d1ffe8ef8567a78d9c2921f254e133fcee746fc88a5069cb381020db77a9ed3
offline replay:            cc3c2ae98ab5e4a6a45aa5b698c7c76facc6f2f16d240e512f0cdf02a931e978
```

The installed logical dataset fingerprints are EUR
`fbf310d9ca5a61aed599ad8ed741d6a71bf406b5d4dff558e17c34510d728b2a`,
HUF `55998069ef59f1fbb831a7f358ddc4bff7456ed7741e4900959f42e3d076d434`,
and combined
`c0ce4d34a6e1398afb43893fc0d27c8e141db15065fdaedf08817f1d09ebe635`.
The installed database SHA-256 after Phase E is
`f7d5dafcb048ce9da9a26bd26268542dcd10c2ec94dc4adf97c3e5fbb3e0f051`;
the canonical release-validator fingerprint against the verified pre-Phase-E
rollback is
`07caa072cf1a7294f265c975bb068da363dee9052fe1163b76c8a9b72e502b25`.

Every manifest links source identity, exact ISIN/currency, identity and chart
raw bytes and receipts, semantic-admission receipt, currency and combined
bundle manifests, imported observations, revision semantics, and deterministic
fingerprints. Identical offline re-import inserts no rows, creates no revision,
and leaves the database byte hash unchanged. Replacements require explicit
append-only provenance; prior evidence is never silently overwritten.

The pre-existing 8,770 legacy NAV observations across 19 ISINs remain
logically unchanged with fingerprint
`b2e6e4b8c2066c932d6933dbb07d8f22ab1fa9e2cd04c88eae7283334829f99a`.
They remain classified as `LEGACY_RETAINED_NOT_PHASE_E_PROVENANCE_ADMITTED`.
All admitted €STR, SOFR, and HUFONIA evidence is unchanged.

## Offline reproduction and validation

Acquisition is a separate explicit operator-only network action. Normal
replay, import, candidate construction, and validation are offline:

```bash
poetry run python scripts/acquire_phase_e_nav.py --offline-audit
poetry run python scripts/build_phase_e_nav_candidate.py \
  --source database/portfolio_advisor.sqlite \
  --candidate /absolute/path/phase-e-candidate.sqlite
poetry run python scripts/import_phase_e_nav.py \
  --target /absolute/path/phase-e-candidate.sqlite
poetry run python scripts/audit_milestone_11c_phase_e.py
```

For a release reconciliation, pass the verified pre-Phase-E rollback as
`--legacy-source`; this binds the audit to both the unchanged legacy logical
fingerprint and the original source-database byte hash. The default installed
audit compares the installed database's legacy projection to itself and still
enforces the fixed legacy row/ISIN/dataset fingerprint.

Raw responses, transport receipts, semantic receipts, and bundle manifests are
immutable local evidence under ignored `data/raw/` paths. Automated tests do
not call the network. Missing/corrupt artifacts, hash/manifest mismatches,
wrong identity/currency, malformed values/dates, duplicates, conflicts,
unauthorized revisions, insufficient history, staleness, and partial schema
all fail closed.

Phase E makes the EUR and HUF evidence cohorts ready for a later Phase F
analytics/construction checkpoint. Governed aligned returns, covariance-aware
portfolio metrics, cash-return alignment, Sharpe/Sortino, runtime construction,
finalist persistence, Milestones 12–13, trading, and production cutover remain
unavailable or unauthorized.
