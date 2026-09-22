# ADR-004: Preserve shortlist classification evidence with an effective mapping

## Status

Accepted

## Context

The admitted shortlist dataset contains the exact source-reported sub-asset
label `Fejl?d? piacok` in 1,278 occurrences across 33 snapshots. The approved
interpretation is `Fejlődő piacok`. The normalized occurrence column and raw
JSON are retained evidence, while construction grouping and persistence
validation previously read the normalized source text directly.

Updating the source rows would break source reconciliation. A display-only
substitution would leave construction and persistence with different meanings,
and a global text normalization rule would silently extend this one authorized
correction to future or unrelated data.

## Decision

Retain the normalized source columns and raw JSON unchanged. Add an immutable,
versioned correction admission bound to the installed shortlist dataset
fingerprint, integration version, original database hash, authorization,
reason, and the deterministic set of stable workbook SHA-256, sheet, row,
snapshot, ISIN, asset-class, and original-label identities.

Expose original and effective values through
`v_effective_shortlist_classification`. Only the exact source value
`Fejl?d? piacok` maps to `Fejlődő piacok`; already-correct and unrelated values
pass through unchanged. Construction evidence, diversification grouping, and
new persistence validation use the same resolver and bind the correction-set
fingerprint into candidate provenance. A historical constructed artifact
without that binding continues to validate against its original
classification rather than being silently reinterpreted.

An exact replay is a no-op only when every admission binding matches. A
same-dataset copy-on-write re-import must reproduce all stable bindings;
changed evidence fails closed and requires a new explicit authorization.

## Consequences

Application grouping and reporting use one traceable effective label while the
source remains exactly reconcilable. Conflict flags and all eligibility and
diversification rules remain unchanged. The current live dataset has no
already-correct occurrences, so the mapping renames groups without changing
their membership or cardinality; it does not prove any existing selection was
wrong. Future datasets do not inherit the correction silently, and restoring
the local corrected state requires the database backup in addition to the
Git-tracked implementation.
