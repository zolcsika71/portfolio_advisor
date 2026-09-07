# Milestone 11C Phase F3A synthetic portfolio-wealth foundation

Phase F3A implements a pure, deterministic EUR portfolio-wealth derivation for
synthetic fixtures only. The bounded implementation records its foundation state as
`SYNTHETIC_WEALTH_FOUNDATION_IMPLEMENTED`. That state is not runtime activation:
it does not make any real share class eligible, admit evidence, construct a real
candidate, or activate ranking, selection, persistence, rebalancing, trading, or
production.

## Governed boundary

`portfolio_advisor.metrics.portfolio_wealth` validates caller-supplied policy
objects against the complete unchanged Phase F1 metrics policy and reviewed
Capital Defensive construction policy. It accepts exactly eight unique synthetic
identities, EUR-only series, exact positive finite Decimal NAV values, explicit
synthetic provenance, and
`SIMULATED_ACCUMULATING_SHARE_CLASS` semantics. Unknown and simulated distributing
states fail closed because distribution reinvestment and cash-flow treatment are
not implemented.

Every complete supplied series is validated before alignment. Dates must already
be canonical, strictly increasing, and unique. The builder takes only the strict
eight-way intersection and selects the latest endpoint plus the latest possible
start that still satisfies 365 calendar days and 252 observed return intervals in
the same window. Its lineage records the complete common-date count, selected
window, excluded leading dates, staleness, and why the next later start fails. It
does not interpolate, fill, deduplicate, substitute an instrument, or manufacture
a valuation date.

## Wealth and lineage

The synthetic initial capital is a positive finite Decimal. Eight securities each
receive 10% and nominal cash receives 20%. Mathematical units are derived once
from each first selected NAV and are then fixed; they are not executable or rounded
order quantities. Component values drift with NAV while nominal cash stays
constant. Calculations use Decimal precision 50 and `ROUND_HALF_EVEN`, without
intermediate quantization. Corrective implementation v1.0.1 creates a fresh,
fully specified context at every F3A arithmetic boundary: `Emin=-999999`,
`Emax=999999`, `capitals=1`, `clamp=0`, and clear flags. `DivisionByZero`,
`FloatOperation`, `InvalidOperation`, and `Overflow` remain trapped; expected
`Inexact` and `Rounded` signals are not trapped. Builder calculations, complete
recomputation, Q18 checks, audit-fixture generation, and the F3A call into the
unchanged F2 synthetic interface therefore do not inherit caller precision,
rounding, exponent bounds, formatting settings, traps, or flags. The caller's
context and flags are preserved on both return and exception.

Initial allocations, initial wealth, and derived weights must satisfy the exact
Phase F1 reconciliation tolerances; nominal cash is retained without change. The
`DECIMAL_STR_SIGN_DIGITS_EXPONENT_V1` encoding uses the context-independent
round-trip string form of each finite Decimal. It preserves the Decimal object's
sign, coefficient digits, and exponent, distinguishing representations such as
`1E+3` and `1000`, as well as valid trailing-zero variants. This is Decimal-object
representation preservation, not recovery of arbitrary lexical spelling already
discarded when the Decimal was parsed; any separately retained original source
text remains a separate, unchanged evidence field. Source values are not normalized
or quantized.

Calculated canonical output is independently rounded to Q18 as an interchange
scale, not a claim of 18-place economic accuracy. Every output quantization remains
within the governed `5E-19` half-quantum boundary, and the eight independently
serialized security weights plus serialized cash weight must reconcile to one
within `4.5E-18`. The unquantized internal weight sum separately remains within
`1E-40`.

The immutable lineage binds both policy identities and fingerprints, decision
context, all complete input-series fingerprints, the window proof, initial capital,
fixed units, every aligned component value and drifted weight, cash, total wealth,
and deterministic point and lineage fingerprints. Validation does not trust the
hash or a caller flag: it rebuilds the full derivation from the supplied input and
requires exact internal derivation equivalence. Point and lineage fingerprints bind
the unquantized internal calculation, so a sub-Q18 alteration cannot be hidden by
canonical output rounding.

The source-observation scheme is
`PHASE_F3A_SYNTHETIC_NAV_V2`. It replaces the lossy v1 fixed-point encoding;
unverifiable v1 observation fingerprints are not accepted as v2. A representation
change requires rebuilt source fingerprints and a rebuilt lineage, and validation
still compares that lineage with a complete recomputation.

Only after that recomputation does the adapter create an existing Phase F2
`SYNTHETIC_FIXTURE` total-return wealth series. Phase F2 itself is unchanged, its
direct fixture behavior is preserved, and the `ADMITTED_EVIDENCE` terminal block
remains in force.

## Audit and verification

The deterministic audit can be printed without writing an artifact:

```bash
poetry run python scripts/audit_phase_f3a_wealth_foundation.py
```

It contains no retrieval timestamp and records the synthetic lineage, F2 metric
run, and explicit non-activation boundaries. Repeated output must be byte-identical.
The builder has no database, Phase E adapter, provider, construction-runtime,
ranking, or historical reconstruction import.

The released v1.0.0 commit `5c5c77e35d8ef89888e9bd9af17619ae879dc2ac`
reproducibly emitted SHA-256
`67ceca6c9015880f482e73be67441daba45854f0e7fe551fd5b6713af29c406b`
and embedded audit fingerprint
`f775bd416b19a13be9819484256c20d87c96c1cb076b4297d4bf213b206707f0`.
Those values do not match the earlier reported stdout SHA-256
`12752690a6ae5bf872f06c4e72ccce6c8cc6ed1da72a6723fe55806d35412fc6`
and fingerprint
`618a083a1e8cd1b9f25b52081d5ac148a71c977cefde7ffba0f33abf333442a3`.
Neither earlier value occurred in the tracked v1.0.0 tree or its examined ancestry;
this corrective note is their first tracked appearance. The repository therefore
establishes the discrepancy but not its cause, and the original release must not
be represented as having passed verification against those references.

For corrective implementation v1.0.1, the deterministic references are:

- stdout SHA-256:
  `af00676072ae8ba5f6a5e22aba0794059fbaf30c7d6b54b0eccedc8f57e08c0c`
- embedded audit fingerprint:
  `b06f3517ec2edeaa88514d7744ddae0e1aff85a4bf5609f025b9981d8daedfeb`
- reference window: 2025-08-28 through 2026-08-28, with 253 observations,
  252 return intervals, and 365 calendar days

Repeated default-context runs and runs under adversarial ambient precision,
rounding, traps, exponent bounds, and pre-set flags must produce the same audit
bytes while leaving the caller's context unchanged.

## Remaining blockers

Real EUR work remains separately blocked by all eight share classes' unknown
accumulation/distribution suitability, the unadmitted 527-row supplementary NAV
prefix, trusted real-source lineage, additive persistence, and the deferred real
portfolio ranking policy. Newly acquired historical documents are not automatically
evidence available at the fixed Phase F1 decision timestamp; their availability and
applicability would require separate admission.

HUF remains outside Phase F3A and additionally lacks admitted authoritative HUFONIA
day-count and applicability evidence. The existing
`PORTFOLIO_NAV_RECONSTRUCTION_FROZEN_UNRESOLVED` guard is unchanged; Phase F3A is
not connected to that historical/runtime path.
