# Milestone 11C Phase F2 — governed metric computation foundation

Phase F2 implements one deterministic, policy-bound metric engine at
`src/portfolio_advisor/metrics/governed.py`. It accepts a strongly typed,
provenance-bound series of already-governed portfolio total-return wealth
endpoints. It does not construct that wealth series from constituents, allocate
cash, rebalance, repair evidence, or participate in scoring, ranking, selection,
or recommendations. Numeric execution is limited to explicit synthetic
reference fixtures in Phase F2. Caller-supplied admission labels cannot
authorize a real portfolio-wealth result: no trusted admitted portfolio-wealth
lineage artifact exists yet.

The execution boundary requires the released
`CAPITAL_DEFENSIVE_PORTFOLIO_METRICS_POLICY` v1.0.0 and canonical fingerprint
`b0c3540efb50e142dcc9dceee258ffd8054e24e9f986d0bf7a1c84114272b2c4`.
Different policy content, an invalid provenance fingerprint, an unapproved
source state, price-only NAV semantics, non-Decimal values, non-finite or
non-positive wealth, malformed or unordered dates, and every duplicate date
fail closed.

## Implemented metrics

The canonical engine implements:

- adjacent simple portfolio-wealth returns over observed endpoints only;
- geometric total return with direct endpoint-factor reconciliation;
- geometric annualized return using exact elapsed calendar days;
- annualized volatility under the Phase F1 D11 estimator; and
- maximum drawdown as a non-positive observed-wealth loss.

Canonical numeric outputs are quantized to 18 decimal places with
`ROUND_HALF_EVEN`, but that scale is an interchange contract rather than a
claim of economic accuracy. Calculations use a local Decimal context of 50
significant digits, deterministic operation order, no binary floats, and no
intermediate quantization. Reconciliation and zero-denominator decisions use
the unquantized value.

The engine emits explicit `AVAILABLE`, `INSUFFICIENT_DATA`, `INPUT_REJECTED`,
`SEMANTICS_NOT_APPROVED`, `POLICY_BLOCKED`, and `UNSUPPORTED_METRIC` states.
Unavailable results contain no numeric placeholder.

## Observed intervals and D11 scope

Dates must be strictly increasing. Each return interval joins two adjacent
supplied observations and retains its exact calendar-day length and endpoint
fingerprints. No interpolation, fill, nearest-date substitution, resampling,
weekend observation, or synthesized endpoint is created. Admitted portfolio
input must also declare the governed
`LATEST_MINIMAL_COMMON_365D_252_WINDOW` selection contract; Phase F2 validates
that declaration but cannot prove it from a caller-supplied label. Consequently
otherwise eligible admitted input remains `POLICY_BLOCKED` pending a separately
trusted lineage boundary. The admitted-evidence validator still enforces the
365-day span, 252 intervals, cutoff, and maximum 30-day staleness gates before
that terminal policy block.

D11 is only the governed constant-drift/constant-diffusion model-based
estimator for irregular observed intervals:

```text
dt[i] = calendar_days(d[i-1], d[i]) / 365
x[i]  = ln(W[i] / W[i-1])
mu    = sum(x[i]) / sum(dt[i])
variance = sum(((x[i] - mu * dt[i]) ** 2) / dt[i]) / (n - 1)
annualized_volatility = sqrt(variance)
```

No additional square-root annualization is applied. Equal gaps reduce to the
sample standard deviation of log returns divided by the square root of the gap
in years. This is not a model-free realized-volatility estimator and is not
claimed to be universally valid. Formula fixtures need at least two intervals;
an admitted-evidence result additionally requires 252 intervals and 365 days
in the same observed window.

Maximum drawdown is `min(W[t] / running_peak[t] - 1)` over supplied endpoints,
so it is always at most zero. A single observation is insufficient rather than
being reported as zero risk.

## Deliberately blocked metrics and real evidence

Sharpe and Sortino remain `POLICY_BLOCKED`. Phase F1 requires exact
interval-aligned benchmark returns and supplies the Sortino benchmark MAR, but
does not uniquely define irregular-interval excess-return aggregation,
denominator weighting, or ratio annualization. Downside deviation is blocked
for the same reason. Historical VaR and CVaR are `UNSUPPORTED_METRIC` because
Phase F1 supplies no confidence level, quantile rule, sample rule, or tail
conditioning method. Legacy float/equal-period implementations are not reused
as policy authorization.

`phase_e_adapter.py` first runs the complete Phase E offline validator, including
schema, integrity, foreign-key, raw/receipt/manifest and installed-row lineage,
then reads current `ADMITTED_VALIDATED` rows from SQLite in read-only/query-only
mode. It retains exact Decimal and complete manifest/source/observation identity
and deliberately labels those rows
`INSTRUMENT_NAV_PRICE_ONLY` with unknown distribution suitability. The engine
therefore produces no numeric portfolio metric from them. All eight EUR
distribution states remain unknown; HUF also remains blocked by authoritative
HUFONIA convention evidence. No supplementary NAV is admitted in Phase F2.

## Deterministic audit and activation boundary

Run the offline audit with:

```bash
poetry run python scripts/audit_phase_f2_metric_foundation.py \
  --output data/audit/capital_defensive_metric_foundation_validation.json
```

The audit revalidates Phase E read-only, rejects malformed validation summaries,
binds the exact Phase F1 policy and all 22 decisions,
executes deterministic regular, irregular, and flat synthetic references, and
records supported/blocked metrics, numerical rules, failure states, database
fingerprints, and explicit non-activation states. It contains no volatile
timestamp and repeated runs are byte-identical.

Phase F2 state is `METRIC_FOUNDATION_IMPLEMENTED`. This does not mean ranking,
portfolio selection, real portfolio construction, persistence, rebalancing,
trading, or production cutover is activated. Later checkpoints may consume F2
outputs only after separate evidence, methodology, schema, and release gates.
