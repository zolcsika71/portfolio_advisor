# Restricted supplementary NAV foundation, contract 1

`HU0000722442_RESTRICTED_SUPPLEMENTARY_NAV` 1.0.0 records the current user's
explicit direction: permit the complete 68 as observed distributor-source
evidence, retaining exact values/provenance and eight issuer disagreements,
without numerical endorsement or dependent computation. It is a scoped
extension, not a rewrite of F1 or an issuer-correction source policy.
The recording timestamp is actual task recording time; approval time and
authenticated identity are deliberately unset. Installed execution is not
authorized. Current real status remains APPROVED_NOT_ADMITTED.

The planner explicitly receives raw-hash-bound policy and baseline references.
It checks the released F1 fingerprint, exact scoped policy, bound narrative,
all narrative source hashes, full 68 chart/inventory matches, transport receipt,
eight discrepancy bindings, full-key attribution and existing Phase E baseline.
The narrative supplies inherited documentary meaning: hashing does not perform
PDF review or authenticate an issuer. Source tokens, retrieval, historical
cutoff, later review and eventual actual execution times stay separate.
Other 60 are not certified correct. Correction values remain typed unadmitted
comparison evidence, not NAV replacements. Narrative sources are not rewritten.

`plan_supplementary_admission` is non-writing; it hashes/deserializes the same
baseline bytes into query-only memory. Live WAL/journal/SHM or WAL-format files
are rejected with a consistent-snapshot requirement. No disk SQLite connection
is needed for a preview. Plan fingerprints use existing canonical JSON, while
artifact references use raw bytes. Preview serialization grants no execution.

`execute_supplementary_admission` requires a caller-supplied
`ExecutionAuthorization` binding exact plan, policy hash, absolute target and
initial-target hash. Current admission-record schema version 2 preserves that
pre-write hash and authorization reference so an exact idempotent retry reuses
the original authorization even though the post-write store has different bytes. An authorization bound
to an unrelated hash or merely to the current post-write store is not a
substitute. This is trusted only through an owner-controlled boundary
with separate execution authorization, not a hash authenticator. There is no
file parser, persistent selection, discovery, CLI or real authorization instance.
The writer reconstructs the plan and dependencies before touching a target.

Historical admission-record schema version 1 did not store the pre-write
target hash. Its exact record, observations and discrepancies remain eligible
for strict read-only inspection, with that hash reported explicitly unknown.
Version 1 is never eligible for retry: neither `None`, an unrelated hash, nor
the current post-write store hash can reconstruct the missing authorization
binding. Readability grants no execution authority, and no migration, backfill
or live-store rewrite is performed by this compatibility path.

Persistence is a SEPARATE store, never an installed Phase E database. It rejects
foreign schema, creates only restricted_admission/restricted_observation/
restricted_discrepancy and immutable triggers, and atomically inserts one
versioned admission record, 68 observations and 8 comparison links. Retries check
the complete record, rows, links, status and schema; altered/partial/colliding
contents reject. DDL and data roll back together. A failed first creation can
leave an empty SQLite file; it cannot leave admitted partial rows. Admission
time is recorded only during execution and is preserved by idempotent retry.

No views/aliases into nav_observation_version or nav_import_manifest exist.
Status constraints and immutable triggers prevent in-place promotion; strict
readback has no fallback/default status. Records expose no metric model
conversion; for_computation always rejects. Phase E/F1/F2/F3A and ranking
eligibility behavior remains unchanged; minimal adapter SQLite-error translation
and early F2 wrong-type rejection make attempted misuse fail explicitly.
A future governed promotion mechanism is
deliberately absent. Existing F1 total counts do not increase when a separate
restricted store is written. Logical admission unit is all 68 of this instrument,
not a 60-row subset or a transaction spanning unrelated instruments.

Tests synthesize source bytes, narrative records, receipts and databases under
pytest temporary directories. They use the versioned F1 policy but no ignored
real PDFs/receipts/databases. The exact scoped ISIN appears solely because this
foundation is intentionally limited to that policy; test NAVs are synthetic.
Separate targeted adapter/F2/F3A tests verify rejection and existing tests keep
eligible synthetic behavior unchanged. No operational consumer imports this
module. Filesystem safety assumes an operator-controlled local workspace.

Later installed execution still requires separately authorized target selection,
current dependency/baseline validation and a trusted per-plan execution input.
Disable means not calling the writer; retained rows cannot be promoted or
deleted by this API. No UNKNOWN, settlement, return, ranking or distribution
status is changed by this foundation.
