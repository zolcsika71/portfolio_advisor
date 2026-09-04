# Milestone 11C Phase F1 — portfolio-metrics methodology policy

Phase F1 records the human-approved methodology profile for later deterministic
same-currency `CAPITAL_DEFENSIVE` portfolio analytics. The governed artifact is
`CAPITAL_DEFENSIVE_PORTFOLIO_METRICS_POLICY` v1.0.0 at
`data/knowledge/validated_rules/capital_defensive_portfolio_metrics.yaml`, with
fingerprint
`b0c3540efb50e142dcc9dceee258ffd8054e24e9f986d0bf7a1c84114272b2c4`.

This checkpoint implements only the policy artifact, its strict loader and its
contract tests. It does not admit supplementary NAV, calculate a return or
metric, migrate or install a database, construct or rank a real portfolio, or
authorize rebalancing, trading, or production use.

## Fixed decision context and delivery boundary

The research decision timestamp is
`2026-09-04T12:24:23.000000Z`. It is derived from epoch `1788524663` in the
immutable Phase E release commit
`79ab552afdceed7d5feacee42e0a7d1ade2003f8`. It is distinct from the NAV
evidence cutoff `2026-08-31` and from a user-selected investment horizon.

Phase F delivery is EUR-first. HUF remains blocked until authoritative HUFONIA
day-count and applicability evidence is admitted. EUR and HUF runs remain
independent and same-currency; the policy prohibits automated cross-currency
ranking and FX conversion.

## Approved supplementary-NAV boundary

Later admission may append new immutable manifests for every retained observed
NAV in the complete bounded prefix between the deterministic common-window
start and the day before the instrument's existing Phase E admission. Existing
evidence must not be replaced, and an implementation must not select only the
dates that survive the final intersection.

| Currency | Prefix start | Supplement | Resulting observations | Common dates/intervals | Span |
| --- | --- | ---: | ---: | ---: | ---: |
| EUR | 2025-05-23 | 527 | 2,486 | 253/252 | 462 days |
| HUF | 2025-08-14 | 83 | 2,108 | 253/252 | 382 days |

The 527 and 83 figures are cohort totals. The complete per-ISIN counts and
ranges are part of the fingerprinted policy artifact. Phase F1 does not perform
that admission.

## Alignment, portfolio and cash semantics

Only observed endpoints in the strict eight-instrument date intersection are
eligible. Missing constituent dates reject an interval. Interpolation, nearest
dates, proxy substitution and synthesized calendar endpoints are prohibited.
The qualifying calculation window must be the latest minimal window satisfying
both a 365-calendar-day span and 252 aligned return intervals.

Each security receives 10% at the initial endpoint and the retained nominal
cash sleeve receives 20%. Security units then remain fixed, so weights drift
with value under a buy-and-hold model. There is no periodic rebalancing. Cash is
unremunerated and its nominal amount remains constant; €STR is the EUR
risk-free comparator, not an assumed return on that cash.

Portfolio simple returns come from adjacent aligned wealth endpoints. Total
return is their geometric chain and must reconcile to the direct endpoint
wealth ratio. €STR percentage-point observations are divided by 100 and use
the approved observed-segment ACT/360 convention. Missing benchmark coverage
blocks the whole risk-adjusted result. The exactly aligned benchmark return is
also the Sortino minimum acceptable return.

## Governed irregular-interval volatility model

The volatility estimator is explicitly model-based. It assumes a
constant-drift, constant-diffusion process across the admitted irregular
observed intervals. It must not be described as model-free realized volatility
or as universally valid.

For strictly increasing endpoint dates `d[0] ... d[n]` and strictly positive
portfolio wealth `W[0] ... W[n]`:

```text
dt[i] = calendar_days(d[i-1], d[i]) / 365
x[i]  = ln(W[i] / W[i-1])
T     = sum(dt[i])
mu    = sum(x[i]) / T
variance = sum(((x[i] - mu * dt[i]) ** 2) / dt[i]) / (n - 1)
annualized_volatility = sqrt(variance)
```

Year fractions use ACT/365F. The `n - 1` denominator accounts for the one
estimated drift parameter. Annualization is already present in the year
fractions; no additional `sqrt(252)` or `sqrt(365)` is applied. The formula has
a mathematical minimum of two intervals, while the governed production window
requires at least 252 intervals and 365 calendar days.

Missing, non-finite, zero or negative wealth and non-increasing endpoints reject
the whole metric result. Exact zero variance returns zero; a risk-adjusted ratio
with a zero denominator is unavailable.

Phase F2 must test the exact formula, irregular gaps, equal-gap equivalence,
zero variance, invalid wealth, deterministic Decimal behavior and endpoint
reconciliation with synthetic fixtures before any production evidence can use
the estimator.

## Distribution and precision blockers

No admitted or retained field proves accumulation/distribution status for any
of the eight EUR share classes. Tokens in product names are not proof. Price
return is therefore prohibited until every member is either proven accumulating
or has exact, complete and admitted distribution cash flows. The real EUR
candidate remains blocked on this evidence even though its bounded NAV prefix
can provide a qualifying common window.

Source numeric text remains exact. Later calculations must use a local Decimal
context of 50 significant digits with `ROUND_HALF_EVEN`, a deterministic
operation order and no binary floats. There is no explicit intermediate
quantization. Calculated canonical outputs use 18 decimal places only as an
interchange scale—not as 18 places of economic accuracy. Source resolution and
calculation metadata remain mandatory.

The approved reconciliation boundaries are:

| Boundary | Tolerance |
| --- | --- |
| Endpoint wealth versus geometric chain | Relative `1E-40` |
| Derived internal weight sum | Absolute `1E-40` |
| Nine independently serialized component weights | Scale-only `4.5E-18` |
| Persisted numeric versus final quantization | Half quantum `5E-19` |
| Initial/retained nominal cash | Exact |
| Approved target-weight sum | Exact |
| Ranking comparison | Exact unquantized Decimal; no epsilon ties |
| Repeated canonical result | Byte-identical |

## Deferred implementation

Phase F2 may implement only the deterministic synthetic-fixture calculation
engine and its validation after a separate authorization. Supplementary NAV
admission is a distinct evidence checkpoint. A later additive migration must
bind run identity, policy ID/version/fingerprint, decision context, holdings,
cash, NAV and benchmark manifests, aligned intervals, metric observations, and
candidate/result fingerprints without mutating prior evidence.

The new real-portfolio ranking family is required, but its score weights and
thresholds remain deliberately unapproved pending Phase F2 review. Portfolio
construction, candidate persistence, finalist selection, Milestones 12–13,
rebalancing, trading and production cutover remain unavailable or unauthorized.
