# ADR-006: Standardize effective shortlist classifications in English

## Status

Accepted

## Context

The verified shortlist dataset contains 10,833 source occurrences across 33
snapshots. Its current effective classification inventory has 77 asset and
sub-asset pairs expressed through Hungarian labels, English labels, spelling
variants, and historical capitalization variants. The model-portfolio source
provides useful English terminology for categories with matching meanings but
does not prove that every similarly named category is equivalent.

The user reviewed and approved one exact 77-pair mapping. The approved output
contains 51 English pairs, using the model-portfolio terminology where meanings
match and explicit reviewed translations elsewhere. Original normalized
columns, raw payloads, conflict flags, and the v1/v2 correction records must
remain evidence rather than being rewritten. A display-only translation would
not provide common semantics to construction, persistence, Phase E selection,
or re-import validation.

## Decision

Supersede ADR-005 for new admissions with an ordered v3 asset/sub-asset pair
mapping contract. Retain the installed v1 and v2 admission and item records as
the first two immutable stages. Store the reviewed mapping as a versioned JSON
manifest whose 77 entries include the exact prior effective pair, exact result
pair, occurrence and snapshot coverage, reference status, dataset fingerprint,
approved group-count effects, and authorization reference.

Admission creates one immutable item for every mapped stable source occurrence.
Each item binds the source file hash, sheet, row, snapshot, ISIN, original pair,
expected prior effective pair, resulting pair, application order, and mapping
fingerprint. The effective view composes stages in order. NULL classifications
remain NULL and are not converted to named categories. Exact replay requires
all bindings to match and adds nothing. Changed evidence, dataset, prior stage,
mapping, authorization, or counts fails transactionally.

All application consumers use the shared effective selector and one validated
batch binding protected by SQLite `data_version`. Historical constructed
artifacts resolve the exact correction stage recorded in their provenance;
later admissions do not reinterpret them. Same-dataset copy-on-write import
must reproduce all stable bindings. A changed dataset fails closed and does not
inherit this admission automatically.

## Consequences

For the approved dataset, all 10,833 effective occurrences use the reviewed
English terminology, producing 7 asset labels, 38 sub-asset labels, and 51
pairs. Approved capitalization merges reduce 48 groups to 47 in eight
snapshots and to 46 in eight snapshots; seventeen snapshots retain their prior
group count. Eligibility and diversification policies are unchanged.

The database gains immutable v3 admission/item tables, an indexed stable-item
resolution path, and pair-aware ordered stage views. Validators must reconcile
the tracked manifest, all prior correction stages, raw evidence, projected
counts, and snapshot effects. Any future dataset or mapping requires a new
explicit review and authorization; this decision is not a general translation
rule.

This ADR supersedes
[ADR-005](ADR-005-compose-authorized-shortlist-classification-corrections.md)
for new classification admissions while preserving the semantics and
provenance of installed v1/v2 records.
