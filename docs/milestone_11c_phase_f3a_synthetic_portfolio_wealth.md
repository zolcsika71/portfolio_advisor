# Milestone 11C Phase F3A synthetic portfolio-wealth foundation

Phase F3A implements a pure, deterministic EUR portfolio-wealth derivation for
synthetic fixtures only. This release changeset records its foundation state as
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
intermediate quantization. Initial allocations, initial wealth, and derived weights
must satisfy the exact Phase F1 reconciliation tolerances; nominal cash is retained
without change. Source NAV Decimal text retains its supplied resolution. Calculated
canonical output is independently rounded to Q18 as an interchange scale, not a
claim of 18-place economic accuracy. Every output quantization remains within the
governed `5E-19` half-quantum boundary, and the eight independently serialized
security weights plus serialized cash weight must reconcile to one within `4.5E-18`.
The unquantized internal weight sum separately remains within `1E-40`.

The immutable lineage binds both policy identities and fingerprints, decision
context, all complete input-series fingerprints, the window proof, initial capital,
fixed units, every aligned component value and drifted weight, cash, total wealth,
and deterministic point and lineage fingerprints. Validation does not trust the
hash or a caller flag: it rebuilds the full derivation from the supplied input and
requires exact internal derivation equivalence. Point and lineage fingerprints bind
the unquantized internal calculation, so a sub-Q18 alteration cannot be hidden by
canonical output rounding.

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
