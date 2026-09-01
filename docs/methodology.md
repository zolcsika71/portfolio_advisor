# Deterministic capital-preservation methodology

## Decision-time boundary

`CapitalPreservationAdvisor` is a read-only decision-time workflow:

```text
dated snapshot -> allocation-weighted reported indicators -> strict eligibility
-> cross-sectional normalization -> stable ranking -> structured result
```

It reads holdings for one exact observation date from `model_portfolios`. It
does not import, update, or reconstruct an observation. The active policy is
`CAPITAL_PRESERVATION_RANKING_POLICY` v1.0.1, loaded from
`data/knowledge/validated_rules/capital_preservation_ranking.yaml`.

Only approved, point-in-time fields enter the decision. Forward returns,
forward risk metrics, backtest results, future snapshots, and Graphify
inferences are prohibited ranking inputs. Missing/non-finite required metrics
reject the candidate; no numerical fallback is used.

## Reported input semantics

The database contains reported constituent indicators, not a portfolio return
series. `Allocation (%)` is the coverage denominator; a reported field is
allocation-weighted only over holdings where it is present. The resulting
coverage is retained explicitly rather than treating missing values as zero.

| Source field | Portfolio indicator | Role |
|---|---|---|
| `Maximum Drawdown` | `maximum_drawdown` | Required primary risk indicator. |
| `1Y Volatility` | `annualized_volatility` | Required primary risk indicator. |
| `Currency Risk` | `unhedged_allocation` | Supporting risk indicator. |
| `1Y Sharpe Ratio` | `sharpe_ratio` | Supporting risk-adjusted-return indicator. |
| `1 Year` | `return_1y` | Secondary reported-return indicator. |

These are reported snapshot indicators. They are not recomputed volatility,
drawdown, Sharpe, VaR, CVaR, or realized portfolio return.

## Eligibility and ranking

Candidates must satisfy the policy's allocation and required-metric coverage
rules. Eligible candidates are normalized across the eligible universe with
deterministic min-max normalization. Higher-is-better metrics use
`(x - min) / (max - min)`; lower-is-better metrics invert that result. Equal
values receive one. Scores are weighted sums and ties use ascending Unicode
portfolio name.

Maximum drawdown is non-positive: `-0.02` is preferable to `-0.10`, therefore
its direction is `HIGHER_BETTER`. The policy version, fingerprint, input date,
candidate rejection reasons, and score contributions are retained in the
prospective decision record.

## Return-series utilities

`metrics/calculations.py` implements deterministic return-series calculations
for an already approved complete return source. These include compounded and
annualized return, volatility, maximum drawdown, downside deviation, Sharpe,
Sortino, historical VaR, and CVaR. Non-finite or insufficient input fails
closed; ratios are unavailable rather than infinite.

The existence of these formulas does not authorize reconstruction. In
particular, portfolio NAV reconstruction from constituent history remains
frozen until portfolio-specific allocation, timing, currency, distribution,
and duplicate-row semantics are proven.

## Official reference-rate boundary

Milestone 11C Phase B admits exact official ECB €STR observations and immutable
request/raw provenance for EUR. Admission establishes evidence identity only;
it does not establish a benchmark-to-portfolio-date alignment methodology or
authorize risk-adjusted portfolio metrics.

Reference-rate values remain exact `Decimal` evidence. Do not forward-fill an
unknown date, substitute zero or a policy rate, infer holiday applicability,
use binary floating point, or imply a benchmark return for the cash sleeve.
Sharpe and Sortino remain `UNAVAILABLE` until reviewed alignment,
annualization, as-of, revision, and portfolio-return contracts are implemented.

## Graphify boundary

Graphify may preserve source-backed methodology, constraints, and warnings.
It cannot supply a historical weight, price, NAV, rebalance instruction, FX
rate, or realized outcome. `INFERRED` relationships never become executable
financial rules without separately reviewed evidence.

## Validation

The policy contract and methodology validators check deterministic directions,
weights, thresholds, coverage behavior, dominance, and no-look-ahead rules.
They validate decision methodology; they do not establish realized
portfolio-level performance.
