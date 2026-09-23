# ADR-005: Compose authorized shortlist classification corrections

## Status

Accepted

## Context

The retained shortlist contains four current effective sub-asset labels with
literal question marks. The user authorized replacing every literal `?` with
`ő` in those current effective labels. The original provider values, raw JSON,
conflict flags, and the accepted v1 mapping from ADR-004 must remain immutable.

ADR-004 installed a single exact-label admission and intentionally rejected a
second admission. Rewriting that admission would erase the distinction between
the earlier authorization and this later, separately authorized rule. A global
display-only replacement would lack stable evidence bindings and could also
silently affect future datasets.

## Decision

Keep the ADR-004 admission and its item records unchanged. Add an ordered v2
composition contract for later classification admissions. Each admission is
bound to the installed shortlist dataset fingerprint, original pre-write
database hash, explicit authorization and reason, exact expected prior labels
and counts, and the stable source identities of every affected occurrence.

For the authorized admission, replace `?` with `ő` only in
`effective_sub_asset_class`. Store each item's original source value, expected
prior effective value, and resulting effective value. The effective view
applies admissions in deterministic order. Exact replay requires every stored
binding to match and adds no records; mismatches fail without mutation.

Application provenance binds the ordered correction composition. Validation of
historical constructed artifacts resolves the correction stage recorded by the
artifact, so a later admission does not reinterpret its category semantics.
Same-dataset copy-on-write import must reproduce every stable binding. Changed
or unverified evidence fails closed. The replacement is an explicit admission
for the verified dataset, not an automatic rule for future imports.

## Consequences

The current effective labels can be corrected without changing source
evidence or the earlier 1,278-item admission. Construction, persistence, Phase
E selection, and reporting continue to share the effective selector, while new
constructed artifacts carry the aggregate correction provenance.

The database gains immutable composition admission/item tables and an ordered
stage view. Validators must verify both legacy and composed contracts, and
operators must inventory and authorize exact prior labels and counts before an
admission. Future additional corrections require their own explicit admission
and must compose from the then-validated effective state.
