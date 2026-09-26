# BIFF-XLS recovery and formula evidence verifier v1

## Status and authority

Implemented by `portfolio_advisor.workbook_source.biff_evidence` and
`scripts/verify_biff_xls_evidence.py`. This is a read-only, hash-bound evidence
verifier for the 33 retained workbooks in `data/xls/processed`. It does not
normalize or admit data, grant a recovery exception, approve formula origin,
connect to SQLite, move a workbook, or alter the watcher.

Every successful report has status
`READ_ONLY_EVIDENCE_NOT_ADMISSION_APPROVAL` and records all three approval
fields as `NOT_GRANTED`. ADR-007 remains `Proposed`.

Those fields record the verifier's audit-time state and remain unchanged. The
subsequent explicit user approvals dated 2026-09-26 are recorded separately
below. The pure `workbook_source.biff_evidence_approval` evaluator now enforces
those two scopes against explicit in-memory report bytes and workbook identity;
it does not modify the historical report or authorize admission.

The expected inventory is
`data/knowledge/biff_xls_processed_expected_inventory_v1.json`. It binds the
exact filename and full SHA-256 of all 33 workbooks, the strict-open outcome,
the expected allocation-overlap count, per-sheet row counts, data-field count,
and zero expected formula records. Missing, additional, renamed, changed, or
symlinked inputs fail before a successful report is written.

## Independent evidence and cross-checks

The verifier deliberately keeps three methods separate:

1. Its independent CFBF inspector reads the header, DIFAT, FAT, directory,
   MiniFAT-sector chain, root mini-stream chain, and Workbook chain directly
   from bytes. It validates FAT/DIFAT markers and reserved-chain ownership,
   extracts the Workbook stream through its own allocation walk, and records
   the exact overlap signature.
2. Its independent BIFF inspector parses workbook globals and both worksheet
   substreams through explicit EOF records. It reads BOUNDSHEET identity and
   visibility, XF-to-format references, ROW/COLINFO inherited formats, cell
   record coordinates and kinds, explicit BLANK/MULBLANK records, absent-cell
   locations, and formula-related record opcodes.
3. Rust-backed Calamine supplies an independent value view from the same
   in-memory bytes that were hashed and inspected. The published `biff_xls`
   parser is then used only as an explicit comparison for source values, cell
   types, coordinates, XF/format references, row coverage, and workbook-hash
   binding.

Calamine does not prove blank-versus-absent, formula presence, or cell-level
format provenance. Those properties come from the raw BIFF inspection. The
raw inspector does not treat Calamine's missing representation as a cell type.
For numeric NUMBER/RK/MULRK and BOOLERR records, it also decodes the record
value directly before comparison. Text is value-compared through Calamine and
record-type-compared through BIFF.

A successful report requires exact leading-space worksheet names in source
order:

| Index | Exact name | Role |
| ---: | --- | --- |
| 0 | ` modell portfóliók` | `MODEL_PORTFOLIO` |
| 1 | ` shortlist` | `ANALYTICAL_SHORTLIST` |

The verifier rejects truncated records, missing or wrong-type BIFF8 BOF/EOF,
nonzero skipped or trailing BIFF regions, invalid allocation chains or sector
markers, invalid XF references, duplicate or unaccounted cell records (including
explicit blanks), unexpected sheet metadata, row/coordinate differences,
value/type/format differences, or any formula inventory different from the
hash-bound expectation. Errors include bounded coordinates and cause exit
status 2; no successful report is created.

## Recovery finding

Six inventory hashes open strictly. The other 27 fail strict `xlrd` opening
with `CompDocError: Workbook corruption: seen[2] == 4`. For each of those 27,
the root directory entry declares a 512-byte mini-stream beginning at sector 1,
but its FAT chain continues at sector 2 through the complete Workbook chain;
the Workbook directory entry itself begins at sector 2. The verifier requires
the exact per-hash overlap-sector count in the inventory.

Independent extraction can read the complete Workbook substreams and compare
their cells, but that does not make the compound allocation graph valid. Every
recovery-dependent result therefore remains
`RECOVERY_EXCEPTION_APPROVAL_REQUIRED` in the historical evidence report. On
2026-09-26 the user explicitly approved the hash-bound exception documented
below for the 27 listed hashes only. The verifier remains unchanged; the
separate evidence-gate evaluator enforces the exact report, registry, defect,
coverage, and comparison bindings, and changed bytes or signatures fail closed.

## Formula statement and limitation

The formula inventory covers `FORMULA`, `FORMULA3`, `FORMULA4`, `ARRAY`,
`ARRAY2`, `SHRFMLA`, `TABLEOP`, `TABLEOP2`, `TABLEOP_B2`, and formula-result
`STRING` records in workbook globals and both complete worksheet substreams.

“Zero formula records in these bytes” proves that those formula-related BIFF
records are absent from the exact retained Workbook stream and that the
examined explicit cells use non-formula cell-record opcodes. It does not prove
pre-export authoring history, whether a producer previously evaluated a
formula and pasted its value, or any state not encoded in the retained bytes.
On 2026-09-26 the user explicitly approved this current-retained-file finding
as sufficient for the formula-origin evidence gate on the 33 listed hashes.
That approval does not establish upstream authoring history, implement the
gate by itself, or authorize admission. The separate evaluator described below
now enforces the approved retained-byte scope.

## Approved evidence-gate sub-decisions

The user explicitly approved both separately decidable proposals on
2026-09-26, subject to every scope, acceptance, and rejection condition below.
This is a documentation record of user authorization, not an executable
approval embedded in the audit report. The separately implemented evaluator
enforces the recorded conditions without changing the historical evidence
report's `admission_approval`, `recovery_exception_approval`, or
`formula_origin_approval` fields from `NOT_GRANTED`; it also does not grant
admission approval. Each approval is independently scoped and neither implies
the other.

Both approvals are bound to the following reviewed evidence:

- evidence-report file SHA-256
  `892e9748de25237be9151c7cae320eff9459eb6455a490aff403e81570539582`;
- canonical report fingerprint
  `c3e1756bedb2306e0f49cb90601a0591b0a6c1918bfaa9e9507ab4d5cf599b3e`;
- verifier source SHA-256
  `cb002a39988ba22e14f0810421efda221a6e951dfb0dfbe86c45732ada47fcf0`;
- expected-inventory SHA-256
  `f37d4c2267d89c0073c60eb1ab1b43115bb3a1d1de3692c08974ea1dba4573b4`;
  and
- report contract `BIFF_XLS_RECOVERY_FORMULA_EVIDENCE_REPORT` version 1,
  verifier version 1, xlrd 2.0.2, and python-calamine 0.8.2.

The table is the normative hash registry for both approved sub-decisions. The
recovery approval covers exactly the 27 `recovery` rows. The formula-origin-
sufficiency approval covers exactly all 33 rows. `Overlap` is the number of
sectors shared by the root mini-stream FAT chain and the complete Workbook
chain; strict rows have no overlap.

| Retained workbook SHA-256 | Open | Overlap |
| --- | --- | ---: |
| `4643359b2693764ce24d3ed8dc985b2ee40d104e25a27ebed852d94ff82e0004` | recovery | 396 |
| `1162ad9340eae704cbaa059c54de41cf2dace581236cc5b78fd110362c0634a5` | recovery | 396 |
| `c051421872b63fe73fd87f29087d9dcf2c938a55df6b369fc5a65a92581e84db` | recovery | 396 |
| `b299cc7b3aed7ed4c8d7cb21fc2ef1dff5dc36deba455c671f0e3970ce920021` | recovery | 397 |
| `dfa41fb6dc1c9a9c659161226ae53dd741c6a678a155b807db74151ec973cca5` | recovery | 397 |
| `4ece94b61d396a861745032251eb85990ca84dff42f24622124834635c83235a` | recovery | 397 |
| `f8318124002feb7804364eb68fcba6a74a9788d52bb223ec134f29bb814d6faf` | recovery | 396 |
| `646b0b323e1f8523b5e6fa466c7b89a608bc96576e70014b9e9d9d01f3d030f7` | strict | 0 |
| `fadecf0acb478cb4ab4596d1cd509390f0a1d221ee811e05528e5ad6cc173abe` | recovery | 405 |
| `b5ff8fef0b4ea778ed4e6ffc661100fdea82b656f03d7b5c58a5f58cff9ec560` | recovery | 404 |
| `1fe00c6e2c52778cb16b3fd3a698224c672a835ae918c556a2add1c8b51a9ecf` | recovery | 406 |
| `a294fac8c5fd0f4021203484fc1f5b2bf5c67f03ff7346689d716c8e60eb891e` | recovery | 404 |
| `5b0bbd39c85fcb5982a901ff929f4d0698086aff64dd531b4309e80d2a0bfd13` | strict | 0 |
| `8a8023a9aa962506c5f5af7e7e89e0f35bfe7ea2c81b7bd3cc8f4b5ec6ab2008` | recovery | 409 |
| `996916e943b50076a3e31ce7d4d58f9c395f79c4f44bc52eb14cd7a8a5c83e1f` | strict | 0 |
| `2d8bba121219fbcf63d128dcc474915d94e63dfbeb4432f487eb9eeaaa4ffcea` | strict | 0 |
| `3d372d7bba9f7c51ebabdbafe186bf48da876c334dfec9101e3e39417320fb5d` | strict | 0 |
| `dc62ee8f966be6f16dc6063a0d49f1d8db15ef280ac28d4c6d9dcb1d61cd998f` | recovery | 374 |
| `b6c58dd7b731e92c3b452a9b761f86653349b3ab6b209b7000fd3cf663a3625f` | recovery | 379 |
| `a74151e567b5a4b095f302e19411ffac1d191d794a0a1de5898a85f2268ebbed` | recovery | 382 |
| `867096ed3ee82283c267cda3ec49f02caa4f0f96144c3faaeb11ed620344374f` | recovery | 380 |
| `7959bac98498473fd7c29e7647945e50b2a78e20d18ed93cf0c79ebc8fd320e8` | recovery | 380 |
| `4e691f2afeac69b2ba63e8803840d5bcd7890a476c41b87b0afb624b30867c67` | strict | 0 |
| `b7f2d9f8346dabbda3ff0facbb136fdee92e90845f50002ea447b2de5020db8c` | recovery | 385 |
| `c636586a06f2e7862ce2af33b8f810b6759a1dc50def440ac7094afa38a3bc9e` | recovery | 386 |
| `ff49b7244b1c5c8eecbc935fd00922ce8bbbf9df10d87676aefe6182d096b825` | recovery | 386 |
| `c5f5b1bd0945942cfd42b8f4c4b99b8e16554c754bbd8f681f76269be027b69e` | recovery | 387 |
| `08d7d242b0965ad8b166f5a545853d9598ad6cf8b7e43500c004d10d3b32e583` | recovery | 388 |
| `239a564d4afd17f9ad7a78d13923b4f508214db54957e532466be912899b3d98` | recovery | 390 |
| `6b244e04ccb4efd1830cddf188008e7e7ec7e200f169fd1583dd0993908b36cb` | recovery | 419 |
| `fea2ca91d597c520d45cee2de8767be96e32b38f6e5720843f1b0c8b4701cc76` | recovery | 441 |
| `646940a4447fc9287097b8ee1705886b0040fc7af2e488c0054960bcf9d99be5` | recovery | 443 |
| `852f697d73112a53fcd75946c16dbc71c5505eea3f35c43d4681f7fbabd34c73` | recovery | 444 |

### Proposal A: hash-bound recovery exception

**Decision recorded:** explicitly approved by the user on 2026-09-26. The
approved disposition accepts the independently examined allocation defect as
sufficient recovery evidence for only the 27 `recovery` hashes above. It is a
narrow exception for those exact bytes, not a general permission to ignore
compound-document corruption.

The approval applies only when every condition below holds for the candidate
workbook:

1. Its full SHA-256 is one of the 27 recovery hashes above and its source
   filename agrees with the committed inventory entry for that hash.
2. Strict opening fails with the inventory-bound
   `CompDocError: Workbook corruption: seen[2] == 4` outcome.
3. The only accepted allocation defect is the reported
   `ROOT_MINISTREAM_WORKBOOK_CHAIN_OVERLAP`: root start sector 1, root declared
   size 512 bytes, Workbook start sector 2, and overlap equal to the complete
   Workbook chain with the exact per-hash sector count above and the per-hash
   `overlap_sector_fingerprint` recorded in the bound report.
4. Independent CFBF and BIFF inspection reaches the EOF of workbook globals
   and both exact visible worksheets in source order, with no skipped,
   trailing-nonzero, unreadable, unsupported, or unaccounted region or record.
5. Worksheet names, indices, visibility, headers, rows, coordinates, and values
   agree with Calamine and the published parser wherever each cross-check
   exposes them. The raw BIFF inspection and published parser agree on source
   cell-record types, XF/format references, explicit blanks, absent cells, and
   duplicate occurrences, with zero value or cell-property mismatches.
6. The report, verifier, inventory, dependency versions, and workbook bytes
   reproduce the evidence bindings listed above. A later implementation must
   fail closed rather than infer equivalence from a similar error message.

Rejection is mandatory for changed bytes, a hash outside the 27-row scope,
filename/inventory disagreement, a different or additional allocation defect,
a changed overlap signature, incomplete substreams, unsupported structures,
an unreadable region, or any structural/value/type/format comparison mismatch.

This approval authorizes the implemented evidence-gate evaluator to mark the
recovery-evidence gate satisfied for an exact listed hash after enforcing every
condition above. The approval does not repair the malformed CFBF graph, approve
formula-origin sufficiency, normalize or admit data, bind corrections,
authorize a writer, change the watcher, transfer authority, or accept ADR-007.

Approved scope wording (explicit user authorization, 2026-09-26):

> Approve a recovery-evidence exception solely for the 27 full SHA-256 values
> marked `recovery` in the normative hash table of the BIFF-XLS recovery and
> formula evidence verifier v1 contract. The exception applies only when the
> reviewed v1 evidence bindings reproduce, the sole allocation defect and its
> per-hash signature match exactly, both worksheet substreams are completely
> inspected, and all independent comparisons have zero mismatches. Changed or
> unlisted bytes, any other defect, incomplete evidence, or any mismatch fail
> closed. This approval satisfies only the recovery-evidence gate and grants no
> admission, operational-authority, correction, migration, or cutover approval.

### Proposal B: retained-file formula-origin sufficiency

**Decision recorded:** explicitly approved by the user on 2026-09-26. Complete
absence of the enumerated formula-related BIFF records in the exact retained
bytes is sufficient for the formula-origin evidence gate for all 33 hashes
above. The approval treats the verified current-file BIFF state as sufficient
for this narrow gate while explicitly declining to make any claim about
upstream authoring history.

The approval applies only when every condition below holds for the candidate
workbook:

1. Its full SHA-256 is one of all 33 hashes in the normative table and its
   filename agrees with the committed inventory entry.
2. Independent BIFF inspection completely covers workbook globals and both
   worksheet substreams through their EOF records without skipped, unreadable,
   unsupported, or unaccounted regions.
3. The inventory and report both record zero formula records under the
   report's enumerated scope: `FORMULA`, `FORMULA3`, `FORMULA4`, `ARRAY`,
   `ARRAY2`, `SHRFMLA`, `TABLEOP`, `TABLEOP2`, `TABLEOP_B2`, and
   formula-result `STRING` records.
4. The examined explicit cells use non-formula cell-record opcodes, and the
   report, verifier, inventory, dependency versions, and exact workbook bytes
   reproduce the evidence bindings listed above.
5. For a recovery-dependent hash, this decision does not substitute for the
   separately approved recovery exception or its conditions; a future
   eligibility evaluator must enforce both gates.

Rejection is mandatory for changed or unlisted bytes, any formula-related
record, incomplete stream coverage, an unsupported or unreadable structure,
an unexpected record, or failure to reproduce the bound evidence. Future
workbooks require their own hash-bound verification and are outside this
proposal.

This approval establishes only that no covered formula record is encoded in
the exact retained files. It cannot establish whether formulas or calculations
existed before export, whether a value was calculated elsewhere and pasted,
who authored a value, or any state absent from these bytes. If upstream
authoring history or proof of original literal entry is required, the approval
is insufficient and must be rejected or supplemented with external evidence.

This approval authorizes the implemented evidence-gate evaluator to mark the
retained-file formula-origin evidence gate satisfied for an exact listed hash
after enforcing every condition above. The approval does not grant a recovery
exception, validate financial semantics, normalize or admit data, bind
corrections, authorize a writer, change the watcher, transfer authority, or
accept ADR-007.

Approved scope wording (explicit user authorization, 2026-09-26):

> Approve the verifier v1 finding of zero enumerated formula-related BIFF
> records as sufficient for the retained-file formula-origin evidence gate for
> exactly the 33 full SHA-256 values in the normative hash table. This means
> only that those records are absent and the examined cells use non-formula
> record opcodes in the exact retained bytes. It makes no claim about formulas,
> calculations, or value-pasting before export, and it does not prove original
> literal entry. Changed or unlisted bytes, any formula record, incomplete
> evidence, or an unsupported structure fail closed. This approval satisfies
> only this evidence gate and grants no recovery exception, admission,
> operational-authority, correction, migration, or cutover approval.

### Implemented evidence-gate enforcement

`portfolio_advisor.workbook_source.biff_evidence_approval` implements
`BIFF_XLS_EVIDENCE_GATE_EVALUATION` version 1 as a pure function over immutable
report bytes plus an explicit workbook SHA-256 and source filename. It:

- exposes no policy argument or alternate evaluator: callers cannot supply a
  registry, report binding, approval identity, dependency identity, or expected
  aggregate; synthetic tests replace the private fixed-policy constant only
  inside their test process;
- accepts only the exact historical report-file SHA-256 and recomputed
  canonical fingerprint, report/verifier/parser versions, verifier-source and
  inventory hashes, dependency versions, aggregate totals, and ordered 33-hash
  registry recorded above;
- inspects the underlying allocation, strict/recovery, sheet/EOF, cell/type,
  XF/format, blank/absent, comparison, and formula fields instead of trusting a
  report verdict or caller-supplied pass flag;
- grants `BOUNDED_EXCEPTION_GRANTED` only to the 27 recovery rows reproducing
  their exact permitted signature, and returns `NOT_REQUIRED_STRICT_OPEN` for
  the six strict rows without granting them an exception;
- returns formula outcome `SUFFICIENT_FOR_RETAINED_BYTES` only when complete
  covered-record evidence remains zero, preserving the documented upstream-
  history limitation; and
- emits an immutable deterministic result whose `admission_approval` is always
  `NOT_GRANTED`.

Malformed, duplicated-key, non-finite, incomplete, contradictory, unsupported,
tampered, unlisted, renamed, or mismatched evidence fails closed with stable
gate-specific reason codes. The evaluator has no filesystem, workbook,
database, writer, watcher, correction, or admission surface. It does not turn
either evidence verdict into overall eligibility. A matching caller-supplied
hash and filename select an entry in the bound historical report; the evaluator
does not open, hash, or freshly inspect a current workbook. Every result records
`fresh_workbook_inspection = NOT_PERFORMED` and scopes its verdicts to that
historical report.

### Gates that remain unresolved

These two approvals leave every non-evidence policy and operational gate
unresolved. Those include real-source model numeric-zero semantics;
currency-risk translation; dispositions for the 24 currency-risk and three
sustainability warnings; `3yr`/`5yr` return interpretation; correction
preservation or rebinding; real-workbook eligibility and writer design;
admission rehearsal; operational authority; migration; and cutover.
The synthetic Phase 3B.1 writer's approved atomic dual-sheet behavior does not
authorize a real-workbook writer or admission. ADR-007 remains
`Proposed`, and `admission_approval` remains `NOT_GRANTED`.

## Deterministic report

Run from the repository root using the configured Poetry environment:

```bash
REPORT_DIR="$(mktemp -d /tmp/portfolio-advisor-biff-evidence.XXXXXX)"
poetry run python scripts/verify_biff_xls_evidence.py \
  --output "$REPORT_DIR/biff-xls-recovery-formula-evidence-v1.json"
shasum -a 256 "$REPORT_DIR/biff-xls-recovery-formula-evidence-v1.json"
```

The output parent must already exist, the output path must be fresh and outside
the repository, and the command uses exclusive creation so a path created
during verification is not overwritten. The report excludes local paths,
timestamps, and raw financial row values. It includes:

- report contract, verifier version, verifier-source SHA-256, dependency
  versions, and committed-inventory SHA-256;
- every workbook filename, full SHA-256, byte length, strict/recovery result,
  allocation signature, worksheet/EOF coverage, cell-property counts, formula
  count, and evidence verdict;
- aggregate row, field, type, formula, and mismatch counts;
- bounded formula or discrepancy coordinates when present; and
- a canonical `report_fingerprint` computed without that fingerprint field.

The pretty-printed file bytes and canonical report fingerprint are deterministic
for the same verifier source, locked dependencies, inventory, and workbook
bytes. Detailed generated evidence remains local and must not be committed.
