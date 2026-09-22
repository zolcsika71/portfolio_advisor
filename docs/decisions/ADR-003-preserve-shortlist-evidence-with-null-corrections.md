# ADR-003: Preserve shortlist evidence with explicit NULL corrections

## Status

Accepted

## Context

The installed shortlist dataset contains 23,979 source-reported numeric zeros in
twelve performance and risk fields. The normalized
`instrument_metric_observation.value` column is `NOT NULL`, and existing
reconciliation proves those values match immutable workbook evidence. Updating
or deleting those rows would destroy that reconciliation, while globally
interpreting every present or future zero as missing would turn a one-time
cleaning instruction into an undocumented import policy.

The authorized requirement is to expose the verified current zeros as missing
without claiming that the source values were erroneous. Corrections must remain
shortlist-specific, provenance-bound, replay-safe, and unable to attach
silently to a changed re-import.

## Decision

Retain source payloads and normalized metric observations unchanged. Add an
immutable shortlist correction admission whose identity is bound to the
shortlist dataset fingerprint, integration version, original pre-write database
SHA-256, authorization reference, reason, and deterministic set of stable
source references. Each correction asserts an expected original numeric zero
and an explicit SQL `NULL` replacement.

Expose corrected values through
`v_effective_shortlist_metric_observation`. Shortlist consumers use that view
when the feature is installed and preserve `None` as unavailable data. The
ordinary importer continues to preserve source zeros. A same-dataset
copy-on-write re-import must reproduce and revalidate every correction by
stable provenance; a changed dataset fails closed and requires a separately
authorized correction admission rather than inheriting or losing corrections.

Exact replay requires the original admission bindings and performs no writes.
The original pre-write hash is retained for that purpose and is never replaced
with the post-admission database hash.

## Consequences

Original workbook reconciliation remains exact and the cleaned analytical
meaning is explicit and auditable. Model-portfolio metrics and other
provenances are unaffected. Readers must handle missing metric values rather
than coercing them to zero, and changed shortlist datasets cannot be published
over the corrected store until their correction applicability is explicitly
reviewed. Recovery requires both Git-tracked implementation and a retained
backup of the local database because the financial store itself is not in Git.
