# Synthetic NAV-correction evidence fixtures

The focused tests build all candidate bytes, receipts, raw JSON artifacts and
SQLite snapshots under pytest temporary directories. Nothing here is retained
market evidence, an admission record, or an operational selector input.

Contract version 1 supports exactly these observation-reference profiles:

- `PHASE_E_SQLITE_V1`: a snapshot-local `nav_observation_version` row plus its
  `nav_import_manifest` identity and fingerprints;
- `RETAINED_RAW_JSON_V1`: an exact JSON value locator plus a separately hashed
  `NAV_OBSERVATION_REFERENCE_BINDING` record that binds the full key; and
- `LEGACY_SQLITE_V1`: a snapshot-local legacy row, its stable source key and a
  separately hashed binding record for share-class/full-key facts absent from
  that table.

Raw and legacy binding records are caller-supplied structured relationships.
Their successful validation does not authenticate an issuer, establish
historical availability or transform either occurrence into Phase E evidence.
Legacy row IDs are meaningful only with the exact database snapshot hash.

`RETAINED_RAW_JSON_V1` also has one closed provider-specific compatibility
shape for the retained Erste Market chart chain. It is recognized only by the
exact `series_locator`, provider observation identity, chart artifact,
quarantine transport receipt, semantic receipt, identity artifact, identity
receipt, binding and raw-value fields. The chart parser retains integer epoch
milliseconds and constructs finite JSON fractional/exponent number tokens
directly as `Decimal`; the generic JSON parser continues to reject floats.
The epoch milliseconds are interpreted as UTC exactly as in the retained chart
contract, without binary-float timestamp division.

The quarantine transport and identity receipts retain their original
`retrieval_timestamp` fields and exact observed envelopes. The semantic
receipt has no retrieval timestamp of its own: it explicitly binds the chart
artifact to the transport receipt and its in-memory media/identity assessment.
It is never converted into an acquisition event. In particular, its admitted
date range may exclude a selected raw-prefix occurrence; that remains a
reported limitation rather than being expanded by compatibility parsing.
Identity/share-class/date/value checks available in structured chart bytes are
verified. The separate full-key binding still supplies unit, value-type and
precision assertions absent from the chart response, and remains a
caller-supplied relationship rather than documentary authentication.

Acquisition events use the closed synthetic profile
`NAV_CORRECTION_ACQUISITION_RECEIPT`, schema version 1. Its
`/retrieved_at_utc` field is verified against the event. It records actual
retrieval of bound bytes; it is not copied into a publication field.

The validator also recognizes the retained, closed compatibility profile
`DISTRIBUTION_EVIDENCE_ACQUISITION_RECEIPT`, schema version 1. Its root has
exactly `schema_version`, `record_type`, `artifact`, and `retrieved_at_utc`;
no additional metadata is permitted. The artifact object has exactly `path`
and `sha256`. Those values must match the acquisition event's normalized
artifact path and raw hash, while the event's full `ArtifactReference` still
supplies and verifies the artifact byte count. The selected receipt artifact
itself remains bound by its exact path, raw hash, and byte count. Its timestamp
locator is exactly `/retrieved_at_utc` and must equal the event timestamp.
Mixed modern/retained artifact aliases are rejected rather than normalized.
The distribution-evidence receipt profile does not alias or parse chart
transport/semantic receipts. Those are handled only by the distinct closed
chart compatibility shape above.

Both receipt profiles preserve acquisition provenance only. Neither establishes
publication authenticity, historical availability, issuer authority,
documentary meaning, correction precedence, admission, or operational use.

Correction artifacts may use a strict JSON pointer, where the full key and
exact decimal text are structurally checked, or a `DOCUMENT_LOCATOR`. A
document locator preserves a page/section claim without interpreting document
prose. The latter therefore remains an explicitly unverified semantic claim.

Candidate inspection is read-only and fail-closed. Structural success grants
no source approval, documentary sufficiency, correction precedence, admission,
operational eligibility or persistent state change.
